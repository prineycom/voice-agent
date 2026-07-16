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
# (1) delegate runs a task to completion; state → done with the result stored.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_delegate_runs_task_to_done_and_stores_result(
    monkeypatch, fake_acp_exec
):
    from conftest import FakeAcpProc

    proc = FakeAcpProc()
    proc.script_prompt(result={"text": "на улице солнечно, плюс восемнадцать"})
    mgr = make_manager(monkeypatch, proc, fake_acp_exec)

    ack = await mgr.delegate("посмотри погоду")
    assert "id:" in ack.lower() or "фон" in ack.lower()  # ack carries the task id

    await mgr.join()

    task = only_task(mgr)
    assert task.state == "done"
    assert "солнечно" in (task.result or "")
    assert task.first_result.done()
    assert "солнечно" in task.first_result.result()


# --------------------------------------------------------------------------- #
# (2) max_concurrent respected: 3 running + 1 queued; queued promotes on finish.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_max_concurrent_queues_and_promotes_on_finish(
    monkeypatch, fake_acp_exec
):
    from conftest import FakeAcpProc

    proc = FakeAcpProc()
    # sess-1 finishes quickly (a short streamed step); sess-2/3 stay busy; the
    # promoted task (sess-4) finishes on its own.
    proc.script_prompt(session_id="sess-1", updates=[acp_tool_call("t", "step")], delay=0.15)
    proc.script_prompt(session_id="sess-2", updates=[acp_tool_call("t", "busy")], delay=5)
    proc.script_prompt(session_id="sess-3", updates=[acp_tool_call("t", "busy")], delay=5)
    proc.script_prompt(session_id="sess-4", result={"text": "promoted done"})

    mgr = make_manager(monkeypatch, proc, fake_acp_exec, max_concurrent=3, max_queued=5)

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

    mgr = make_manager(monkeypatch, proc, fake_acp_exec, max_concurrent=1, max_queued=1)

    await mgr.delegate("a")  # running
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
    mgr = make_manager(monkeypatch, proc, fake_acp_exec)

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

    mgr = make_manager(monkeypatch, proc, fake_acp_exec, max_concurrent=3)
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

    mgr = make_manager(monkeypatch, proc, fake_acp_exec, max_concurrent=2, max_queued=5)

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
