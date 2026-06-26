"""Tests for the async Hermes delegation manager (HermesTaskManager).

No real subprocess and no real LiveKit session are used: asyncio.create_subprocess_exec
is monkeypatched with controllable FakeProcs, and a FakeSession records the
proactive generate_reply() calls the manager makes. This keeps the tests
CI-friendly (no Hermes install, no Pi, no SFU) while exercising the real
queueing / progress / cancellation logic.
"""

import asyncio
import json

import pytest

from hermes_tasks import HermesTaskManager


class FakeHandle:
    """Stand-in for a SpeechHandle — awaitable, resolves immediately."""

    def __await__(self):
        async def _noop():
            return None

        return _noop().__await__()


class FakeSession:
    """Records proactive replies; mimics AgentSession.generate_reply + idle state.

    Defaults to idle (agent listening, user not speaking, no current speech) so
    the delivery worker speaks immediately; tests set ``agent_state`` /
    ``user_state`` to simulate a busy conversation and exercise idle-gating.
    """

    def __init__(self):
        self.replies: list[str] = []
        self.agent_state = "listening"
        self.user_state = "listening"
        self.current_speech = None

    def generate_reply(self, *, instructions=None, allow_interruptions=None, **kwargs):
        self.replies.append(instructions or "")
        return FakeHandle()


class FakeProc:
    """Controllable stand-in for asyncio.subprocess.Process.

    communicate() blocks until ``release`` is set (default: pre-set, returns at
    once). kill() unblocks it so a cancelled/timed-out task can finish tearing down.
    """

    def __init__(self, *, stdout=b"", stderr=b"", returncode=0, blocking=False):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.killed = False
        self.release = asyncio.Event()
        if not blocking:
            self.release.set()

    async def communicate(self):
        await self.release.wait()
        if self.killed:
            self.returncode = -9
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True
        self.release.set()


def fake_exec_factory(procs):
    """Return a create_subprocess_exec replacement that hands out ``procs`` in order
    and records the argv of each spawn in ``calls``."""
    queue = list(procs)
    calls: list[list[str]] = []

    async def _fake(*args, **kwargs):
        calls.append(list(args))
        return queue.pop(0)

    _fake.calls = calls
    return _fake


@pytest.mark.asyncio
async def test_delegate_returns_immediately_and_delivers_result(monkeypatch):
    session = FakeSession()
    proc = FakeProc(stdout=b"weather is sunny\n", stderr=b"session_id: s1\n", returncode=0, blocking=True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec_factory([proc]))

    mgr = HermesTaskManager(task_timeout=10)
    mgr.attach_session(session)

    directive = await mgr.delegate("посмотри погоду")

    # Returned before the subprocess finished, and nothing spoken yet.
    assert "background" in directive.lower() or "фон" in directive.lower()
    assert session.replies == []

    # Let Hermes finish; the result is delivered proactively via generate_reply.
    proc.release.set()
    await mgr.join()

    assert any("weather is sunny" in r for r in session.replies)


@pytest.mark.asyncio
async def test_delegate_builds_hermes_command(monkeypatch):
    session = FakeSession()
    proc = FakeProc(stdout=b"ok\n", returncode=0)
    fake = fake_exec_factory([proc])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)

    mgr = HermesTaskManager(task_timeout=10)
    mgr.attach_session(session)
    await mgr.delegate("найди погоду в Москве")
    await mgr.join()

    argv = fake.calls[0]
    assert argv[0] == "hermes"
    assert "chat" in argv
    assert "найди погоду в Москве" in argv  # passed as a single -q argument
    assert "-Q" in argv and "--yolo" in argv


@pytest.mark.asyncio
async def test_exceeding_concurrency_queues_until_slot_frees(monkeypatch):
    session = FakeSession()
    procs = [FakeProc(stdout=f"r{i}\n".encode(), blocking=True) for i in range(3)]
    fake = fake_exec_factory(procs)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)

    mgr = HermesTaskManager(max_concurrent=2, max_queued=5, task_timeout=10)
    mgr.attach_session(session)

    await mgr.delegate("a")
    await mgr.delegate("b")
    d3 = await mgr.delegate("c")
    await asyncio.sleep(0.02)  # let the admitted tasks reach create_subprocess_exec

    assert len(fake.calls) == 2  # third is queued, not spawned
    assert "очеред" in d3.lower() or "queue" in d3.lower()

    procs[0].release.set()  # free a slot
    await asyncio.sleep(0.02)
    assert len(fake.calls) == 3  # queued task now started

    procs[1].release.set()
    procs[2].release.set()
    await mgr.join()


@pytest.mark.asyncio
async def test_queue_overflow_refuses(monkeypatch):
    session = FakeSession()
    procs = [FakeProc(stdout=b"x\n", blocking=True) for _ in range(10)]
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec_factory(procs))

    mgr = HermesTaskManager(max_concurrent=1, max_queued=1, task_timeout=10)
    mgr.attach_session(session)

    await mgr.delegate("a")  # running
    await mgr.delegate("b")  # queued (fills the queue)
    refusal = await mgr.delegate("c")  # overflow

    assert "слишком много" in refusal.lower() or "подожд" in refusal.lower()

    for p in procs:
        p.release.set()
    await mgr.join()


@pytest.mark.asyncio
async def test_no_voice_nudges_while_task_runs(monkeypatch):
    """A long-running task must NOT emit any proactive speech until it finishes —
    progress nudges flooded the speech queue, so they are gone."""
    session = FakeSession()
    proc = FakeProc(stdout=b"finally done\n", blocking=True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec_factory([proc]))

    mgr = HermesTaskManager(task_timeout=10)
    mgr.attach_session(session)
    await mgr.delegate("долгая задача")

    await asyncio.sleep(0.2)
    assert session.replies == []  # nothing spoken while it runs

    proc.release.set()
    await mgr.join()
    assert any("finally done" in r for r in session.replies)  # result delivered at the end


@pytest.mark.asyncio
async def test_results_coalesced_into_one_reply_when_several_ready(monkeypatch):
    """Several results ready at once are delivered as a SINGLE coalesced reply,
    not one generate_reply per task."""
    session = FakeSession()
    # Hold the session busy so both tasks finish before any delivery happens.
    session.agent_state = "speaking"
    p1 = FakeProc(stdout=b"result one\n")
    p2 = FakeProc(stdout=b"result two\n")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec_factory([p1, p2]))

    mgr = HermesTaskManager(max_concurrent=2, task_timeout=10)
    mgr.attach_session(session)
    await mgr.delegate("задача один")
    await mgr.delegate("задача два")
    await asyncio.sleep(0.05)  # both subprocesses finish, results buffered

    assert session.replies == []  # nothing delivered while busy

    session.agent_state = "listening"  # conversation goes idle
    await mgr.join()

    assert len(session.replies) == 1  # one coalesced reply, not two
    assert "result one" in session.replies[0] and "result two" in session.replies[0]


@pytest.mark.asyncio
async def test_delivery_waits_until_session_idle(monkeypatch):
    session = FakeSession()
    session.user_state = "speaking"  # user is talking — do not interrupt
    proc = FakeProc(stdout=b"ready now\n")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec_factory([proc]))

    mgr = HermesTaskManager(task_timeout=10)
    mgr.attach_session(session)
    await mgr.delegate("задача")
    await asyncio.sleep(0.05)
    assert session.replies == []  # held back while the user speaks

    session.user_state = "listening"
    await mgr.join()
    assert any("ready now" in r for r in session.replies)


@pytest.mark.asyncio
async def test_long_output_is_trimmed_before_delivery(monkeypatch):
    session = FakeSession()
    huge = ("a" * 5000).encode()
    proc = FakeProc(stdout=huge)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec_factory([proc]))

    mgr = HermesTaskManager(task_timeout=10)
    mgr.attach_session(session)
    await mgr.delegate("болтливая задача")
    await mgr.join()

    assert session.replies, "a result should be delivered"
    # The delivered instruction must not carry the full 5000-char dump.
    assert len(session.replies[0]) < 2000


@pytest.mark.asyncio
async def test_cancel_kills_running_task_and_delivers_no_result(monkeypatch):
    session = FakeSession()
    proc = FakeProc(stdout=b"should not be delivered\n", blocking=True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec_factory([proc]))

    mgr = HermesTaskManager(task_timeout=10)
    mgr.attach_session(session)
    await mgr.delegate("задача")
    await asyncio.sleep(0.02)  # let it reach communicate()

    msg = await mgr.cancel()
    await mgr.join()

    assert proc.killed is True
    assert not any("should not be delivered" in r for r in session.replies)
    assert "отмен" in msg.lower()
    assert mgr._running == 0


@pytest.mark.asyncio
async def test_cancel_with_no_active_tasks_says_so(monkeypatch):
    mgr = HermesTaskManager()
    mgr.attach_session(FakeSession())
    msg = await mgr.cancel()
    assert "нет" in msg.lower()


@pytest.mark.asyncio
async def test_shutdown_cancels_all_running_and_queued(monkeypatch):
    session = FakeSession()
    procs = [FakeProc(stdout=b"x\n", blocking=True) for _ in range(5)]
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec_factory(procs))

    mgr = HermesTaskManager(max_concurrent=2, max_queued=5, task_timeout=10)
    mgr.attach_session(session)
    await mgr.delegate("a")
    await mgr.delegate("b")
    await mgr.delegate("c")  # queued
    await asyncio.sleep(0.02)

    await mgr.shutdown()

    assert mgr._running == 0
    assert list(mgr._queue) == []
    assert procs[0].killed and procs[1].killed  # the two running were killed


@pytest.mark.asyncio
async def test_timeout_cancels_and_reports_failure(monkeypatch):
    session = FakeSession()
    proc = FakeProc(stdout=b"never\n", blocking=True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec_factory([proc]))

    mgr = HermesTaskManager(task_timeout=0.05)
    mgr.attach_session(session)
    await mgr.delegate("медленная задача")
    await mgr.join()

    assert proc.killed is True
    assert any("время" in r.lower() or "не успел" in r.lower() for r in session.replies)


@pytest.mark.asyncio
async def test_nonzero_exit_reports_error(monkeypatch):
    session = FakeSession()
    proc = FakeProc(stdout=b"", stderr=b"boom failure\n", returncode=2)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec_factory([proc]))

    mgr = HermesTaskManager(task_timeout=10)
    mgr.attach_session(session)
    await mgr.delegate("сломается")
    await mgr.join()

    assert any("ошибк" in r.lower() for r in session.replies)


@pytest.mark.asyncio
async def test_list_tasks_summarizes_running_and_queued(monkeypatch):
    session = FakeSession()
    procs = [FakeProc(stdout=b"x\n", blocking=True) for _ in range(3)]
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec_factory(procs))

    mgr = HermesTaskManager(max_concurrent=1, max_queued=5, task_timeout=10)
    mgr.attach_session(session)
    await mgr.delegate("поиск погоды")
    await mgr.delegate("проверка почты")  # queued
    await asyncio.sleep(0.02)

    summary = mgr.list_tasks()
    assert "погод" in summary.lower()
    assert "почт" in summary.lower()

    for p in procs:
        p.release.set()
    await mgr.join()


@pytest.mark.asyncio
async def test_delegate_tool_uses_manager_from_session_userdata(monkeypatch):
    """The @function_tool adapter pulls the manager from session.userdata, binds
    the session, and delegates — returning the manager's directive."""
    import worker_tools
    from types import SimpleNamespace

    proc = FakeProc(stdout=b"sunny\n", returncode=0)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec_factory([proc]))

    mgr = HermesTaskManager(task_timeout=10)
    session = FakeSession()
    session.userdata = mgr
    ctx = SimpleNamespace(session=session)

    out = await worker_tools.delegate_to_hermes("посмотри погоду", context=ctx)
    assert "background" in out.lower() or "фон" in out.lower()
    await mgr.join()
    assert any("sunny" in r for r in session.replies)


@pytest.mark.asyncio
async def test_publishes_ui_events_and_task_snapshot(monkeypatch):
    """The manager publishes a 'delegated' event, a 'done' event carrying the full
    output, and at least one task snapshot — for the web UI to render."""
    published = []

    async def publisher(data: bytes):
        published.append(json.loads(data))

    proc = FakeProc(stdout=b"sunny, plus eighteen degrees\n")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec_factory([proc]))

    mgr = HermesTaskManager(task_timeout=10, output_limit=10)  # tiny limit → summary != full
    mgr.attach_session(FakeSession())
    mgr.set_publisher(publisher)

    await mgr.delegate("посмотри погоду")
    await mgr.join()
    await asyncio.sleep(0.02)  # flush fire-and-forget publish tasks

    kinds = [(e.get("type"), e.get("kind")) for e in published]
    assert ("event", "delegated") in kinds
    assert ("event", "done") in kinds
    assert any(e.get("type") == "tasks" for e in published)

    done = next(e for e in published if e.get("kind") == "done")
    assert "sunny, plus eighteen degrees" in done["full"]  # full, untrimmed
    assert len(done["summary"]) <= 11  # trimmed to output_limit (+ ellipsis)


@pytest.mark.asyncio
async def test_no_publisher_is_safe(monkeypatch):
    """With no publisher set, the manager runs normally (publishing is a no-op)."""
    proc = FakeProc(stdout=b"ok\n")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec_factory([proc]))
    mgr = HermesTaskManager(task_timeout=10)
    mgr.attach_session(FakeSession())
    await mgr.delegate("задача")
    await mgr.join()  # must not raise
