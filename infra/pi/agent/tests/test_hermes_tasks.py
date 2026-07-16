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
        self.reply_calls: list[dict] = []  # full kwargs per generate_reply call
        self.said: list[dict] = []  # recorded say() calls (milestone narration)
        self.agent_state = "listening"
        self.user_state = "listening"
        self.current_speech = None
        # Backs wait_for_idle: set = conversation idle (the default). Tests
        # clear it to model a permanently busy conversation.
        self.idle_event = asyncio.Event()
        self.idle_event.set()

    def generate_reply(self, *, instructions=None, allow_interruptions=None, **kwargs):
        self.replies.append(instructions or "")
        self.reply_calls.append(
            {"instructions": instructions, "allow_interruptions": allow_interruptions}
        )
        return FakeHandle()

    async def wait_for_idle(self):
        """Mirrors AgentSession.wait_for_idle: a coroutine that resolves once the
        session is idle (blocks while busy). Divergence from the real API: returns
        None instead of the AgentActivity — the manager ignores the value."""
        await self.idle_event.wait()

    def say(self, text, *, allow_interruptions=None, add_to_chat_ctx=True, **kwargs):
        """Mirrors AgentSession.say (synchronous, returns a SpeechHandle)."""
        self.said.append(
            {
                "text": text,
                "allow_interruptions": allow_interruptions,
                "add_to_chat_ctx": add_to_chat_ctx,
            }
        )
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


@pytest.mark.asyncio
async def test_delegate_cancelled_mid_window_cleans_up_and_promotes(
    monkeypatch, fake_acp_exec
):
    """Cancelling delegate() itself mid-window (framework barge-in cancels the
    in-flight tool call) must clear the handshake state — the task keeps running
    and, when it settles, its completion hooks fire normally (queued promotes)."""
    from conftest import FakeAcpProc

    proc = FakeAcpProc()
    # sess-1 settles on its own at ~0.3s (past the delegate cancellation below);
    # sess-2 is the queued task that must still get promoted afterwards.
    proc.script_prompt(
        session_id="sess-1", updates=[acp_tool_call("t", "step")],
        result={"text": "поздний ответ"}, delay=0.3,
    )
    proc.script_prompt(session_id="sess-2", result={"text": "promoted"})

    mgr = make_manager(
        monkeypatch, proc, fake_acp_exec, max_concurrent=1, max_queued=5, fast_window_s=5
    )

    racer = asyncio.create_task(mgr.delegate("долгая"))
    await asyncio.sleep(0.05)  # window open, task admitted and running
    first = only_task(mgr)
    assert first.awaiting_sync is True

    q = await mgr.delegate("вторая")  # slot full → queued immediately
    assert "очеред" in q.lower()

    racer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await racer

    # The handshake state is cleaned even though neither win nor timeout fired.
    assert first.awaiting_sync is False
    assert first.state == "running"  # the underlying task was NOT cancelled

    # When the task settles, _on_task_complete runs the hooks (not a permanent
    # deferral) → the queued task is promoted and completes.
    second = [t for t in mgr._tasks.values() if t is not first][0]
    assert await wait_for(lambda: second.state == "done")
    assert first.state == "done"
    assert first.completion_pending is False
    assert first.delivered_synchronously is False


@pytest.mark.asyncio
async def test_cancel_during_open_window_returns_cancelled_ack(
    monkeypatch, fake_acp_exec
):
    """cancel() on a task whose fast window is still open → delegate wakes at once
    with a cancelled ack, NOT the background directive (no result is coming)."""
    from conftest import FakeAcpProc

    proc = FakeAcpProc()
    proc.script_prompt(updates=[acp_tool_call("t", "busy")], delay=5)
    mgr = make_manager(monkeypatch, proc, fake_acp_exec, fast_window_s=5)

    racer = asyncio.create_task(mgr.delegate("долгая"))
    await wait_for(lambda: len(mgr._tasks) == 1)
    task = only_task(mgr)
    assert await wait_for(lambda: task.state == "running" and task.awaiting_sync)

    msg = await mgr.cancel()
    assert "отмен" in msg.lower()

    # The racer wakes immediately (first_result released), well before the 5s
    # window, and answers honestly — no background-result promise.
    reply = await asyncio.wait_for(racer, timeout=1.0)
    assert task.state == "cancelled"
    assert "отмен" in reply.lower()
    assert task.task_id in reply
    assert "фон" not in reply.lower()  # not DIRECTIVE_BACKGROUND
    assert "придёт" not in reply.lower()
    assert task.delivered_synchronously is False
    assert task.awaiting_sync is False
    # No dangling pending future (its resolution was consumed by the racer).
    assert task.first_result.done()


# --------------------------------------------------------------------------- #
# Milestone narration (ADR-0022 "Progress"): short template phrases via
# session.say on tool START edges — never in chat_ctx, never over the user.
# --------------------------------------------------------------------------- #
def make_tool_event(
    kind_of_update="tool_call",
    title="terminal: ls",
    kind="execute",
    status="pending",
):
    """Build an AcpToolEvent like the ones acp_client parses from the stream."""
    from acp_client import AcpToolEvent

    return AcpToolEvent(
        kind_of_update=kind_of_update,
        tool_call_id="tc-1",
        title=title,
        kind=kind,
        status=status,
        raw={},
    )


def running_task(mgr, request="тестовая задача"):
    """Register a task in `running` state without spawning an ACP driver —
    _on_tool_event only touches live state + narration, so no runner is needed."""
    task = mgr._make_task(request)
    task.state = "running"
    return task


@pytest.mark.asyncio
async def test_narrates_tool_start_when_idle(monkeypatch, fake_acp_exec):
    """Streamed tool_call events (behavioural, via the scripted ACP peer) narrate
    once the fast window has closed and the channel is free."""
    from conftest import FakeAcpProc

    from hermes_tasks import NARRATION_BY_KIND, NARRATION_BY_TOOL

    proc = FakeAcpProc()
    # First tool event lands ~instantly (inside the 0.1s window → silent), the
    # second at ~0.3s (window closed → narrated), result at ~0.6s.
    proc.script_prompt(
        updates=[
            acp_tool_call("t1", "terminal: ls"),
            acp_tool_call("t2", "web: поиск погоды", kind="fetch"),
        ],
        result={"text": "готово"},
        delay=0.3,
    )
    mgr = make_manager(monkeypatch, proc, fake_acp_exec, fast_window_s=0.1)

    await mgr.delegate("проверь погоду")
    await mgr.join()

    session = mgr._session
    assert len(session.said) == 1  # in-window event silent, post-window narrated
    call = session.said[0]
    assert call["text"] == NARRATION_BY_KIND["fetch"]
    assert call["add_to_chat_ctx"] is False  # never enters the conversation context
    assert call["allow_interruptions"] is True
    # Known template vocabulary only — no free-form/LLM text.
    assert call["text"] in (
        set(NARRATION_BY_KIND.values()) | set(NARRATION_BY_TOOL.values())
    )


@pytest.mark.asyncio
async def test_no_narration_when_channel_busy_but_state_updates(
    monkeypatch, fake_acp_exec
):
    from conftest import FakeAcpProc

    mgr = make_manager(monkeypatch, FakeAcpProc(), fake_acp_exec)
    task = running_task(mgr)
    mgr._session.agent_state = "speaking"  # channel busy

    mgr._on_tool_event(task, make_tool_event(title="terminal: df -h"))

    assert mgr._session.said == []  # skipped, not queued
    assert task.last_tool == "terminal: df -h"  # live state still updated
    assert task.steps == 1


@pytest.mark.asyncio
async def test_narration_dedupes_same_kind_and_rate_limits(
    monkeypatch, fake_acp_exec
):
    from conftest import FakeAcpProc

    mgr = make_manager(monkeypatch, FakeAcpProc(), fake_acp_exec)
    task = running_task(mgr)

    # Two same-kind starts back-to-back → exactly one phrase.
    mgr._on_tool_event(task, make_tool_event(title="terminal: ls"))
    mgr._on_tool_event(task, make_tool_event(title="terminal: pwd"))
    assert len(mgr._session.said) == 1
    assert task.steps == 2

    # A different kind arriving inside NARRATION_MIN_GAP_S is rate-limited too.
    mgr._on_tool_event(task, make_tool_event(title="web: график", kind="fetch"))
    assert len(mgr._session.said) == 1

    # Past the gap, a different kind narrates again.
    task.last_narrated_at -= 100  # rewind the throttle clock
    mgr._on_tool_event(task, make_tool_event(title="web: график", kind="fetch"))
    assert len(mgr._session.said) == 2


@pytest.mark.asyncio
async def test_no_narration_while_fast_window_open(monkeypatch, fake_acp_exec):
    from conftest import FakeAcpProc

    mgr = make_manager(monkeypatch, FakeAcpProc(), fake_acp_exec)
    task = running_task(mgr)
    task.awaiting_sync = True  # fast window open: user silently awaits the answer

    mgr._on_tool_event(task, make_tool_event())

    assert mgr._session.said == []
    assert task.last_tool == "terminal: ls"  # live state still updated
    assert task.steps == 1


@pytest.mark.asyncio
async def test_unknown_kind_narrates_generic_phrase(monkeypatch, fake_acp_exec):
    from conftest import FakeAcpProc

    mgr = make_manager(monkeypatch, FakeAcpProc(), fake_acp_exec)
    task = running_task(mgr, request="разобрать почту")

    mgr._on_tool_event(
        task, make_tool_event(title="странный инструмент", kind="mystery")
    )

    assert len(mgr._session.said) == 1
    text = mgr._session.said[0]["text"]
    assert "работаю" in text
    assert task.label in text  # generic phrase carries the task label


@pytest.mark.asyncio
async def test_completion_edge_does_not_narrate(monkeypatch, fake_acp_exec):
    from conftest import FakeAcpProc

    mgr = make_manager(monkeypatch, FakeAcpProc(), fake_acp_exec)
    task = running_task(mgr)

    mgr._on_tool_event(
        task,
        make_tool_event(
            kind_of_update="tool_call_update", title=None, kind=None, status="completed"
        ),
    )

    assert mgr._session.said == []  # finish edges are progress data, not milestones
    assert task.steps == 1


# --------------------------------------------------------------------------- #
# Synthetic tool-turn reintegration (ADR-0022): background results become a
# paired FunctionCall + FunctionCallOutput ("task_result") in chat_ctx — never
# a role="system" note (the 0007 re-delegation bug).
# --------------------------------------------------------------------------- #
def tool_turn_pair(session):
    """The (calls, outputs) inserted into the session agent's chat context."""
    from livekit.agents.llm import FunctionCall, FunctionCallOutput

    items = session.current_agent.chat_ctx.items
    calls = [i for i in items if isinstance(i, FunctionCall)]
    outs = [i for i in items if isinstance(i, FunctionCallOutput)]
    return calls, outs


@pytest.mark.asyncio
async def test_background_result_reintegrated_as_tool_turn(
    monkeypatch, fake_acp_exec
):
    from conftest import FakeAcpProc

    proc = FakeAcpProc()
    proc.script_prompt(
        updates=[acp_tool_call("t", "step")], result={"text": "ответ из фона"}, delay=0.3
    )
    mgr = make_manager(monkeypatch, proc, fake_acp_exec, fast_window_s=0.05)
    session = FakeSessionWithAgent()
    mgr.attach_session(session)

    directive = await mgr.delegate("долгая задача")
    task = only_task(mgr)
    assert task.task_id in directive  # backgrounded

    await mgr.join()
    assert await wait_for(lambda: task.reintegrated)

    calls, outs = tool_turn_pair(session)
    assert len(calls) == 1 and len(outs) == 1  # exactly one pair
    call, out = calls[0], outs[0]
    assert call.call_id == out.call_id == f"hermes_task_{task.task_id}"
    assert call.name == "task_result" and out.name == "task_result"
    assert out.is_error is False
    assert out.output == task.result  # the stored (trimmed) answer, verbatim
    args = json.loads(call.arguments)
    assert args["task_id"] == task.task_id
    assert "долгая" in args["request"]
    # The 0007 anti-pattern must be gone: nothing glued in as a system message.
    assert not any(
        getattr(i, "role", None) == "system"
        for i in session.current_agent.chat_ctx.items
    )


@pytest.mark.asyncio
async def test_background_failure_reintegrated_as_error(monkeypatch, fake_acp_exec):
    from conftest import FakeAcpProc

    proc = FakeAcpProc()
    proc.script_prompt(
        updates=[acp_tool_call("t", "step")],
        error={"code": -32000, "message": "hermes boom"},
        delay=0.3,
    )
    mgr = make_manager(monkeypatch, proc, fake_acp_exec, fast_window_s=0.05)
    session = FakeSessionWithAgent()
    mgr.attach_session(session)

    await mgr.delegate("сломается в фоне")
    task = only_task(mgr)
    await mgr.join()
    assert task.state == "failed"
    assert await wait_for(lambda: task.reintegrated)

    calls, outs = tool_turn_pair(session)
    assert len(calls) == 1 and len(outs) == 1
    assert outs[0].is_error is True
    assert "boom" in outs[0].output.lower()  # honest error text, same tool shape


@pytest.mark.asyncio
async def test_synchronous_result_is_not_reintegrated(monkeypatch, fake_acp_exec):
    """A fast-window win already returned the answer as the REAL tool result —
    a synthetic copy would duplicate it in context."""
    from conftest import FakeAcpProc

    proc = FakeAcpProc()
    proc.script_prompt(result={"text": "мгновенный ответ"})
    mgr = make_manager(monkeypatch, proc, fake_acp_exec)  # default 8s window
    session = FakeSessionWithAgent()
    mgr.attach_session(session)

    answer = await mgr.delegate("быстрый вопрос")
    task = only_task(mgr)
    assert "мгновенный" in answer
    assert task.delivered_synchronously is True

    await mgr.join()
    await asyncio.sleep(0.05)  # give any (wrong) spawned finalize a chance to run

    assert task.reintegrated is False
    assert session.current_agent.chat_ctx.items == []  # chat_ctx untouched


@pytest.mark.asyncio
async def test_cancelled_task_reintegrated_minimally(monkeypatch, fake_acp_exec):
    from conftest import FakeAcpProc

    proc = FakeAcpProc()
    proc.script_prompt(updates=[acp_tool_call("t", "busy")], delay=5)
    mgr = make_manager(monkeypatch, proc, fake_acp_exec, fast_window_s=0.05)
    session = FakeSessionWithAgent()
    mgr.attach_session(session)

    await mgr.delegate("долгая задача")  # backgrounds after the tiny window
    task = only_task(mgr)
    await mgr.cancel(task.task_id)
    assert task.state == "cancelled"
    assert await wait_for(lambda: task.reintegrated)

    calls, outs = tool_turn_pair(session)
    assert len(calls) == 1 and len(outs) == 1
    assert outs[0].call_id == f"hermes_task_{task.task_id}"
    assert outs[0].is_error is True  # never readable as an answer
    assert "отмен" in outs[0].output.lower()  # minimal "task cancelled" record


@pytest.mark.asyncio
async def test_reintegration_failure_survives_and_logs(
    monkeypatch, fake_acp_exec, caplog
):
    import logging

    from conftest import FakeAcpProc

    class ExplodingAgent(FakeAgent):
        async def update_chat_ctx(self, chat_ctx, **kwargs):
            raise RuntimeError("ctx boom")

    proc = FakeAcpProc()
    proc.script_prompt(
        updates=[acp_tool_call("t", "step")], result={"text": "готово"}, delay=0.3
    )
    mgr = make_manager(monkeypatch, proc, fake_acp_exec, fast_window_s=0.05)
    session = FakeSessionWithAgent()
    session.current_agent = ExplodingAgent()
    mgr.attach_session(session)

    with caplog.at_level(logging.ERROR, logger="agent"):
        await mgr.delegate("долгая задача")
        task = only_task(mgr)
        await mgr.join()
        assert await wait_for(
            lambda: any(
                "reintegration failed" in r.getMessage() for r in caplog.records
            )
        )

    # The manager survives: task state intact, no crash, still serviceable.
    assert task.state == "done"
    assert task.reintegrated is False
    assert mgr.list_tasks()  # still answers status queries


class SlowUpdateAgent(FakeAgent):
    """FakeAgent whose update_chat_ctx really suspends — mirrors the realtime-LLM
    path where the replace-style update awaits, opening a lost-update window for
    an unsynchronized copy()→insert()→update sequence."""

    def __init__(self, delay=0.15):
        super().__init__()
        self._delay = delay

    async def update_chat_ctx(self, chat_ctx, **kwargs):
        await asyncio.sleep(self._delay)
        self._ctx = chat_ctx


@pytest.mark.asyncio
async def test_concurrent_reintegrations_keep_both_pairs(monkeypatch, fake_acp_exec):
    """Two background tasks finishing near-simultaneously must BOTH land their
    tool-turn pair: update_chat_ctx REPLACES the context, so without the
    chat_ctx lock the second finalizer's stale copy() overwrites the first's."""
    from conftest import FakeAcpProc

    proc = FakeAcpProc()
    proc.script_prompt(
        updates=[acp_tool_call("t", "step")], result={"text": "ответ один"}, delay=0.3
    )
    proc.script_prompt(
        updates=[acp_tool_call("t", "step")], result={"text": "ответ два"}, delay=0.3
    )
    mgr = make_manager(monkeypatch, proc, fake_acp_exec, fast_window_s=0.05)
    session = FakeSessionWithAgent()
    session.current_agent = SlowUpdateAgent(delay=0.15)
    mgr.attach_session(session)

    await mgr.delegate("задача один")
    await mgr.delegate("задача два")
    t1, t2 = list(mgr._tasks.values())

    await mgr.join()
    assert await wait_for(lambda: t1.reintegrated and t2.reintegrated)

    calls, outs = tool_turn_pair(session)
    assert len(calls) == 2 and len(outs) == 2  # neither pair overwritten
    assert {c.call_id for c in calls} == {
        f"hermes_task_{t1.task_id}",
        f"hermes_task_{t2.task_id}",
    }
    assert {o.output for o in outs} == {"ответ один", "ответ два"}


@pytest.mark.asyncio
async def test_shutdown_drains_inflight_finalizer(monkeypatch, fake_acp_exec):
    """A task that genuinely finishes right before shutdown must still land its
    chat_ctx record: shutdown drains finalizers instead of cancelling them."""
    from conftest import FakeAcpProc

    proc = FakeAcpProc()
    proc.script_prompt(
        updates=[acp_tool_call("t", "step")],
        result={"text": "успел до выключения"},
        delay=0.3,
    )
    mgr = make_manager(monkeypatch, proc, fake_acp_exec, fast_window_s=0.05)
    session = FakeSessionWithAgent()
    session.current_agent = SlowUpdateAgent(delay=0.1)  # finalizer mid-update
    mgr.attach_session(session)

    await mgr.delegate("долгая задача")
    task = only_task(mgr)
    await mgr.join()  # task done; its finalizer just spawned / mid-await
    await mgr.shutdown()  # must drain the finalizer, not cancel it

    assert task.state == "done"
    assert task.reintegrated is True  # the record landed despite the shutdown
    calls, outs = tool_turn_pair(session)
    assert len(calls) == 1 and len(outs) == 1
    assert outs[0].output == "успел до выключения"


# --------------------------------------------------------------------------- #
# Bounded delivery (ADR-0022): after reintegration, speak the report via
# generate_reply — natural pause up to delivery_fallback_s, else soft barge-in.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_delivery_speaks_promptly_when_idle(monkeypatch, fake_acp_exec):
    from conftest import FakeAcpProc

    proc = FakeAcpProc()
    proc.script_prompt(
        updates=[acp_tool_call("t", "step")], result={"text": "готово"}, delay=0.3
    )
    # Generous fallback (5s): if delivery wrongly sat out the window while idle,
    # the wait_for (2s) below would time out.
    mgr = make_manager(
        monkeypatch, proc, fake_acp_exec, fast_window_s=0.05, delivery_fallback_s=5
    )
    session = FakeSessionWithAgent()
    mgr.attach_session(session)

    await mgr.delegate("проверь погоду")
    task = only_task(mgr)
    await mgr.join()
    assert await wait_for(lambda: task.delivered)

    assert task.reintegrated is True  # hard ordering: record first, speech after
    assert len(session.replies) == 1
    instr = session.replies[0]
    assert task.label in instr and task.task_id in instr  # tied to the context record
    assert "кстати" not in instr.lower()  # idle path → no barge-in phrasing
    assert session.reply_calls[0]["allow_interruptions"] is True


@pytest.mark.asyncio
async def test_delivery_barges_in_after_fallback_when_busy(
    monkeypatch, fake_acp_exec
):
    from conftest import FakeAcpProc

    proc = FakeAcpProc()
    proc.script_prompt(
        updates=[acp_tool_call("t", "step")], result={"text": "готово"}, delay=0.3
    )
    mgr = make_manager(
        monkeypatch, proc, fake_acp_exec, fast_window_s=0.05, delivery_fallback_s=0.1
    )
    session = FakeSessionWithAgent()
    session.idle_event.clear()  # permanently busy: wait_for_idle never resolves
    mgr.attach_session(session)

    await mgr.delegate("долгая задача")
    task = only_task(mgr)
    await mgr.join()
    # The report still lands ~delivery_fallback_s later — never lost to a busy
    # conversation (the old unbounded idle-gate bug).
    assert await wait_for(lambda: task.delivered)

    assert len(session.replies) == 1
    assert "кстати, по той задаче" in session.replies[0].lower()  # soft barge-in


@pytest.mark.asyncio
async def test_delivery_reports_failure_honestly(monkeypatch, fake_acp_exec):
    from conftest import FakeAcpProc

    proc = FakeAcpProc()
    proc.script_prompt(
        updates=[acp_tool_call("t", "step")],
        error={"code": -32000, "message": "hermes boom"},
        delay=0.3,
    )
    mgr = make_manager(monkeypatch, proc, fake_acp_exec, fast_window_s=0.05)
    session = FakeSessionWithAgent()
    mgr.attach_session(session)

    await mgr.delegate("сломается в фоне")
    task = only_task(mgr)
    await mgr.join()
    assert await wait_for(lambda: task.delivered)

    assert task.state == "failed"
    instr = session.replies[0].lower()
    assert "не удалась" in instr  # honest failure report
    assert "честно" in instr
    assert "не выдумывай успех" in instr


@pytest.mark.asyncio
async def test_cancelled_task_is_not_delivered(monkeypatch, fake_acp_exec):
    """cancel() already speaks its own confirmation via the tool result — the
    finalizer writes the context record but must not also report proactively."""
    from conftest import FakeAcpProc

    proc = FakeAcpProc()
    proc.script_prompt(updates=[acp_tool_call("t", "busy")], delay=5)
    mgr = make_manager(monkeypatch, proc, fake_acp_exec, fast_window_s=0.05)
    session = FakeSessionWithAgent()
    mgr.attach_session(session)

    await mgr.delegate("долгая задача")
    task = only_task(mgr)
    await mgr.cancel(task.task_id)
    assert await wait_for(lambda: task.reintegrated)  # record still written

    assert session.replies == []  # no proactive report
    assert task.delivered is False


@pytest.mark.asyncio
async def test_deliver_without_session_is_safe(monkeypatch, fake_acp_exec):
    from conftest import FakeAcpProc

    mgr = make_manager(monkeypatch, FakeAcpProc(), fake_acp_exec)
    task = mgr._make_task("тест")
    task.state = "done"
    task.result = "результат"
    mgr._session = None

    await mgr._deliver(task)  # must not raise

    assert task.delivered is False  # nothing spoken; result stays in live state


@pytest.mark.asyncio
async def test_delivery_failure_survives_and_logs(monkeypatch, fake_acp_exec, caplog):
    import logging

    from conftest import FakeAcpProc

    class ExplodingReplySession(FakeSessionWithAgent):
        def generate_reply(self, **kwargs):
            raise RuntimeError("reply boom")

    proc = FakeAcpProc()
    proc.script_prompt(
        updates=[acp_tool_call("t", "step")], result={"text": "готово"}, delay=0.3
    )
    mgr = make_manager(monkeypatch, proc, fake_acp_exec, fast_window_s=0.05)
    session = ExplodingReplySession()
    mgr.attach_session(session)

    with caplog.at_level(logging.ERROR, logger="agent"):
        await mgr.delegate("долгая задача")
        task = only_task(mgr)
        await mgr.join()
        assert await wait_for(
            lambda: any("delivery failed" in r.getMessage() for r in caplog.records)
        )

    # Reintegration ran FIRST, so the failed delivery lost only the speech.
    assert task.reintegrated is True
    assert task.delivered is False
    assert task.state == "done"
    calls, outs = tool_turn_pair(session)
    assert len(calls) == 1 and len(outs) == 1  # answer safe in chat_ctx
