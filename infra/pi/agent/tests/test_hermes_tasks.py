"""Tests for the ACP-based Hermes task manager (HermesTaskManager, ADR-0022).

No real Hermes process and no real LiveKit session are used. The manager drives a
REAL :class:`acp_client.AcpClient` whose ``asyncio.create_subprocess_exec`` is
monkeypatched with the scripted :class:`conftest.FakeAcpProc` peer (real ACP
transport, scripted answers, every message recorded). This keeps the tests
behavioural — the manager's admission / live-state / cancel / UI logic runs
against the same codec production uses — while staying CI-friendly (no Hermes
install, no Pi, no SFU).
"""

import asyncio
import json

import pytest

from acp_client import AcpClient
from conftest import acp_tool_call, acp_tool_call_update
from hermes_tasks import HermesTaskManager


# --------------------------------------------------------------------------- #
# Fakes reused/extended by this and later tasks in the chain (delivery /
# reintegration write into a chat context and speak proactive replies).
# --------------------------------------------------------------------------- #
class FakeHandle:
    """Stand-in for a SpeechHandle — awaitable, resolves immediately."""

    def __await__(self):
        async def _noop():
            return None

        return _noop().__await__()


class FakeSession:
    """Records proactive replies; mimics AgentSession.generate_reply + idle state.

    Defaults to idle (agent listening, user not speaking, no current speech) so a
    later delivery worker speaks immediately; tests set ``agent_state`` /
    ``user_state`` to simulate a busy conversation.
    """

    def __init__(self):
        self.replies: list[str] = []
        self.agent_state = "listening"
        self.user_state = "listening"
        self.current_speech = None

    def generate_reply(self, *, instructions=None, allow_interruptions=None, **kwargs):
        self.replies.append(instructions or "")
        return FakeHandle()


class FakeChatCtx:
    """Minimal stand-in for llm.ChatContext (copy + add_message + insert)."""

    def __init__(self, items=None):
        self.items = list(items or [])

    def copy(self):
        return FakeChatCtx(self.items)

    def add_message(self, *, role, content, **kwargs):
        self.items.append((role, content))

    def insert(self, items):
        """Record inserted items (synthetic tool-turn reintegration, later task)."""
        if isinstance(items, (list, tuple)):
            self.items.extend(items)
        else:
            self.items.append(items)


class FakeAgent:
    """Stand-in for the live Agent: exposes a copyable chat_ctx + update_chat_ctx."""

    def __init__(self):
        self._ctx = FakeChatCtx()

    @property
    def chat_ctx(self):
        return self._ctx

    async def update_chat_ctx(self, chat_ctx, **kwargs):
        self._ctx = chat_ctx


class FakeSessionWithAgent(FakeSession):
    """FakeSession that also exposes current_agent, for chat-context injection."""

    def __init__(self):
        super().__init__()
        self.current_agent = FakeAgent()


# --------------------------------------------------------------------------- #
# Harness: a manager wired to a real AcpClient over a scripted FakeAcpProc.
# --------------------------------------------------------------------------- #
def make_manager(monkeypatch, proc, fake_acp_exec, **kwargs):
    """Build a HermesTaskManager over a real AcpClient backed by ``proc``."""
    exec_fn, _created = fake_acp_exec(proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", exec_fn)
    client = AcpClient()
    mgr = HermesTaskManager(client, **kwargs)
    mgr.attach_session(FakeSession())
    return mgr


def only_task(mgr):
    """The single task in a manager (convenience for one-task tests)."""
    tasks = list(mgr._tasks.values())
    assert len(tasks) == 1, f"expected exactly one task, got {tasks}"
    return tasks[0]


async def wait_for(predicate, timeout=2.0, interval=0.01):
    """Poll ``predicate`` until true or the timeout elapses."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return predicate()


# --------------------------------------------------------------------------- #
# (1) fast result within the window → delegate returns the ANSWER synchronously
#     (not an ack), delivered_synchronously, state done, one "done" UI event.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_delegate_fast_result_returns_answer_synchronously(
    monkeypatch, fake_acp_exec
):
    from conftest import FakeAcpProc

    published = []

    async def publisher(data: bytes):
        published.append(json.loads(data))

    proc = FakeAcpProc()
    proc.script_prompt(result={"text": "на улице солнечно, плюс восемнадцать"})
    mgr = make_manager(monkeypatch, proc, fake_acp_exec)  # default 8s window
    mgr.set_publisher(publisher)

    answer = await mgr.delegate("посмотри погоду")

    # The tool returns the reply itself, not a background ack.
    assert "солнечно" in answer
    assert "id:" not in answer.lower() and "фон" not in answer.lower()

    task = only_task(mgr)
    assert task.state == "done"
    assert task.delivered_synchronously is True
    assert task.first_result.done()
    assert "солнечно" in task.first_result.result()

    await mgr.join()
    await asyncio.sleep(0.05)  # flush fire-and-forget UI publishes

    # The "done" UI event still fires — exactly once (re-sends share one id).
    done_ids = {e.get("id") for e in published if e.get("kind") == "done"}
    assert len(done_ids) == 1


# --------------------------------------------------------------------------- #
# (2) max_concurrent respected: 3 running + 1 queued; queued promotes on finish.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_max_concurrent_queues_and_promotes_on_finish(
    monkeypatch, fake_acp_exec
):
    from conftest import FakeAcpProc

    proc = FakeAcpProc()
    # sess-1 finishes on its own after ~0.5s (comfortably past the four delegates'
    # cumulative tiny-window blocking, so all three occupy slots when d is
    # delegated); sess-2/3 stay busy; the promoted task (sess-4) finishes at once.
    proc.script_prompt(session_id="sess-1", updates=[acp_tool_call("t", "step")], delay=0.5)
    proc.script_prompt(session_id="sess-2", updates=[acp_tool_call("t", "busy")], delay=5)
    proc.script_prompt(session_id="sess-3", updates=[acp_tool_call("t", "busy")], delay=5)
    proc.script_prompt(session_id="sess-4", result={"text": "promoted done"})

    # Tiny fast window so each admitted task backgrounds at once (its result is
    # slower than the window) instead of blocking delegate — the concurrency /
    # queue accounting is what this test exercises, not the fast-window race.
    mgr = make_manager(
        monkeypatch, proc, fake_acp_exec, max_concurrent=3, max_queued=5, fast_window_s=0.02
    )

    await mgr.delegate("a")
    await mgr.delegate("b")
    await mgr.delegate("c")
    d = await mgr.delegate("d")  # over capacity → queued

    # State is set synchronously in _launch, so this holds regardless of timing.
    assert mgr._running_count() == 3
    assert len(mgr._queue) == 1
    assert "очеред" in d.lower()

    # sess-1 finishes → a slot frees → the queued task is promoted and runs.
    queued_task = mgr._queue[0] if mgr._queue else None
    assert queued_task is not None
    assert await wait_for(lambda: queued_task.state != "queued")
    assert queued_task.state in ("running", "done")

    await mgr.shutdown()


# --------------------------------------------------------------------------- #
# (3) queue overflow → refusal string.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_queue_overflow_refuses(monkeypatch, fake_acp_exec):
    from conftest import FakeAcpProc

    proc = FakeAcpProc()
    for sid in ("sess-1", "sess-2"):
        proc.script_prompt(session_id=sid, updates=[acp_tool_call("t", "busy")], delay=5)

    mgr = make_manager(
        monkeypatch, proc, fake_acp_exec, max_concurrent=1, max_queued=1, fast_window_s=0.05
    )

    await mgr.delegate("a")  # running (backgrounds after the tiny window)
    await mgr.delegate("b")  # queued (fills the queue)
    refusal = await mgr.delegate("c")  # overflow

    assert "слишком много" in refusal.lower() or "подожд" in refusal.lower()
    # The refused task was never registered.
    assert len(mgr._tasks) == 2

    await mgr.shutdown()


# --------------------------------------------------------------------------- #
# (4) list_tasks shows live last_tool/steps while running, then finished state.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_list_tasks_live_then_finished(monkeypatch, fake_acp_exec):
    from conftest import FakeAcpProc

    proc = FakeAcpProc()
    proc.script_prompt(
        updates=[acp_tool_call("tc1", "terminal: ls"), acp_tool_call_update("tc1")],
        result={"text": "готово"},
        delay=0.25,
    )
    # Tiny window so delegate backgrounds and we can observe the live running
    # state before the (slower) result lands.
    mgr = make_manager(monkeypatch, proc, fake_acp_exec, fast_window_s=0.05)

    await mgr.delegate("проверь диск")
    task = only_task(mgr)

    # After the first streamed tool event lands, the live status reflects it.
    assert await wait_for(lambda: task.last_tool == "terminal: ls" and task.steps >= 1)
    summary = mgr.list_tasks()
    assert "проверь диск" in summary
    assert "terminal: ls" in summary  # live last_tool surfaced
    assert task.state == "running"

    await mgr.join()

    finished = mgr.list_tasks()
    assert task.state == "done"
    assert "готово" in finished  # finished state shown, not "still running"
    assert "в работе" not in finished


# --------------------------------------------------------------------------- #
# (5) cancel by label and by task_id → cancelled + UI event.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_cancel_by_label_and_id(monkeypatch, fake_acp_exec):
    from conftest import FakeAcpProc

    published = []

    async def publisher(data: bytes):
        published.append(json.loads(data))

    proc = FakeAcpProc()
    for sid in ("sess-1", "sess-2"):
        proc.script_prompt(session_id=sid, updates=[acp_tool_call("t", "busy")], delay=5)

    mgr = make_manager(
        monkeypatch, proc, fake_acp_exec, max_concurrent=3, fast_window_s=0.05
    )
    mgr.set_publisher(publisher)

    await mgr.delegate("проверь почту")
    await mgr.delegate("посчитай бюджет")
    mail, budget = list(mgr._tasks.values())

    # Cancel one by label substring.
    msg = await mgr.cancel("почту")
    assert "отмен" in msg.lower()
    assert mail.state == "cancelled"
    assert budget.state == "running"

    # Cancel the other by task_id.
    await mgr.cancel(budget.task_id)
    assert budget.state == "cancelled"

    await asyncio.sleep(0.05)  # flush fire-and-forget UI publishes
    assert any(e.get("kind") == "cancelled" for e in published)

    await mgr.shutdown()


# --------------------------------------------------------------------------- #
# (6) shutdown cancels running tasks and drains the queue.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_shutdown_cancels_running_and_queue(monkeypatch, fake_acp_exec):
    from conftest import FakeAcpProc

    proc = FakeAcpProc()
    for sid in ("sess-1", "sess-2"):
        proc.script_prompt(session_id=sid, updates=[acp_tool_call("t", "busy")], delay=5)

    mgr = make_manager(
        monkeypatch, proc, fake_acp_exec, max_concurrent=2, max_queued=5, fast_window_s=0.05
    )

    await mgr.delegate("a")
    await mgr.delegate("b")
    await mgr.delegate("c")  # queued
    assert mgr._running_count() == 2
    assert len(mgr._queue) == 1

    await mgr.shutdown()

    assert mgr._running_count() == 0
    assert list(mgr._queue) == []
    assert all(t.state == "cancelled" for t in mgr._tasks.values())
    # Idempotent: a second shutdown must not raise.
    await mgr.shutdown()


# --------------------------------------------------------------------------- #
# (7) UI snapshot shape (ops.js compat) + the four feed event kinds.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_ui_snapshot_shape_and_event_kinds(monkeypatch, fake_acp_exec):
    from conftest import FakeAcpProc

    published = []

    async def publisher(data: bytes):
        published.append(json.loads(data))

    proc = FakeAcpProc()
    proc.script_prompt(result={"text": "готово"})
    mgr = make_manager(monkeypatch, proc, fake_acp_exec)
    mgr.set_publisher(publisher)

    await mgr.delegate("задача")
    await mgr.join()
    await asyncio.sleep(0.05)  # flush fire-and-forget publishes

    kinds = [(e.get("type"), e.get("kind")) for e in published]
    assert ("event", "delegated") in kinds
    assert ("event", "done") in kinds

    # Task snapshots keep the ops.js contract: {type:"tasks", running:[{label,
    # elapsed}], queued:[...]} .
    snaps = [e for e in published if e.get("type") == "tasks"]
    assert snaps
    assert all("running" in s and "queued" in s for s in snaps)
    assert any(
        any("label" in r and "elapsed" in r for r in s["running"]) for s in snaps
    )


@pytest.mark.asyncio
async def test_failed_task_emits_error_event(monkeypatch, fake_acp_exec):
    from conftest import FakeAcpProc

    published = []

    async def publisher(data: bytes):
        published.append(json.loads(data))

    proc = FakeAcpProc()
    proc.script_prompt(error={"code": -32000, "message": "hermes boom"})
    mgr = make_manager(monkeypatch, proc, fake_acp_exec)
    mgr.set_publisher(publisher)

    await mgr.delegate("сломается")
    await mgr.join()
    await asyncio.sleep(0.05)

    task = only_task(mgr)
    assert task.state == "failed"
    assert "boom" in (task.result or "").lower()
    kinds = [(e.get("type"), e.get("kind")) for e in published]
    assert ("event", "error") in kinds


@pytest.mark.asyncio
async def test_long_result_trimmed_to_output_limit(monkeypatch, fake_acp_exec):
    from conftest import FakeAcpProc

    proc = FakeAcpProc()
    proc.script_prompt(result={"text": "a" * 5000})
    mgr = make_manager(monkeypatch, proc, fake_acp_exec, output_limit=50)

    await mgr.delegate("болтливая задача")
    await mgr.join()

    task = only_task(mgr)
    assert len(task.result) <= 51  # trimmed to output_limit (+ ellipsis)


@pytest.mark.asyncio
async def test_cancel_with_no_active_tasks_says_so(monkeypatch, fake_acp_exec):
    from conftest import FakeAcpProc

    mgr = make_manager(monkeypatch, FakeAcpProc(), fake_acp_exec)
    msg = await mgr.cancel()
    assert "нет" in msg.lower()


@pytest.mark.asyncio
async def test_no_publisher_is_safe(monkeypatch, fake_acp_exec):
    from conftest import FakeAcpProc

    proc = FakeAcpProc()
    proc.script_prompt(result={"text": "ok"})
    mgr = make_manager(monkeypatch, proc, fake_acp_exec)  # no publisher set

    await mgr.delegate("задача")
    await mgr.join()  # must not raise
    assert only_task(mgr).state == "done"


# --------------------------------------------------------------------------- #
# Fast-window race (ADR-0022): background / failure / queued / handshake.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_delegate_slow_result_backgrounds_then_completes(
    monkeypatch, fake_acp_exec
):
    """Result slower than the window → directive with task_id now, result later."""
    from conftest import FakeAcpProc

    proc = FakeAcpProc()
    # One streamed step, then the result arrives ~0.3s later — past the 0.1s window.
    proc.script_prompt(
        updates=[acp_tool_call("t", "step")], result={"text": "готово позже"}, delay=0.3
    )
    mgr = make_manager(monkeypatch, proc, fake_acp_exec, fast_window_s=0.1)

    directive = await mgr.delegate("долгая задача")
    task = only_task(mgr)

    # Backgrounded: a directive carrying the task id, not the answer.
    assert task.task_id in directive
    assert "готово позже" not in directive
    assert task.delivered_synchronously is False
    assert task.state == "running"

    await mgr.join()

    assert task.state == "done"
    assert "готово позже" in (task.result or "")
    assert task.delivered_synchronously is False


@pytest.mark.asyncio
async def test_delegate_fast_failure_returns_honest_error(monkeypatch, fake_acp_exec):
    """A failure WITHIN the window → an honest failure string (with the error)."""
    from conftest import FakeAcpProc

    proc = FakeAcpProc()
    proc.script_prompt(error={"code": -32000, "message": "hermes boom"})
    mgr = make_manager(monkeypatch, proc, fake_acp_exec)  # default 8s window

    reply = await mgr.delegate("сломается быстро")
    task = only_task(mgr)

    assert task.state == "failed"
    assert task.delivered_synchronously is True
    assert "boom" in reply.lower()  # the error text is surfaced honestly
    assert "не удалась" in reply.lower() or "не получилось" in reply.lower()


@pytest.mark.asyncio
async def test_queued_task_returns_immediately_without_window(
    monkeypatch, fake_acp_exec
):
    """A queued task returns the queued directive at once — it never waits a window."""
    from conftest import FakeAcpProc

    proc = FakeAcpProc()
    # sess-1 occupies the only slot for a long time; sess-2 is the queued task.
    proc.script_prompt(session_id="sess-1", updates=[acp_tool_call("t", "busy")], delay=5)
    proc.script_prompt(session_id="sess-2", result={"text": "не должно ждать"})

    # A generous 5s window: if the queued path wrongly awaited it, this test would
    # hang for ~5s. Fill the running slot directly (bypassing delegate's window) so
    # the next delegate deterministically hits the QUEUE branch.
    mgr = make_manager(
        monkeypatch, proc, fake_acp_exec, max_concurrent=1, max_queued=5, fast_window_s=5
    )
    mgr._start_task("занят")  # running, slot full
    assert mgr._running_count() == 1

    loop = asyncio.get_event_loop()
    t0 = loop.time()
    directive = await mgr.delegate("в очередь")
    elapsed = loop.time() - t0

    assert "очеред" in directive.lower()
    assert elapsed < 1.0  # returned immediately, did not consume the 5s window
    assert len(mgr._queue) == 1
    assert mgr._queue[0].delivered_synchronously is False

    await mgr.shutdown()


@pytest.mark.asyncio
async def test_slow_path_runs_completion_hooks_exactly_once(monkeypatch, fake_acp_exec):
    """The deferred-completion handshake fires the completion side-effects once.

    A task that settles just after the window (so _on_task_complete runs with the
    race already resolved) must emit exactly one "done" UI event and promote/prune
    exactly once — no double-fire from the delegate side of the handshake.
    """
    from conftest import FakeAcpProc

    published = []

    async def publisher(data: bytes):
        published.append(json.loads(data))

    proc = FakeAcpProc()
    proc.script_prompt(
        updates=[acp_tool_call("t", "step")], result={"text": "готово"}, delay=0.3
    )
    mgr = make_manager(monkeypatch, proc, fake_acp_exec, fast_window_s=0.1)
    mgr.set_publisher(publisher)

    directive = await mgr.delegate("долгая")
    task = only_task(mgr)
    assert task.task_id in directive  # backgrounded

    await mgr.join()
    await asyncio.sleep(0.05)  # flush fire-and-forget publishes

    # Exactly one distinct "done" event (its re-sends share a single id).
    done = [e for e in published if e.get("kind") == "done"]
    assert done
    assert len({e.get("id") for e in done}) == 1
    assert task.state == "done"
    assert task.completion_pending is False
