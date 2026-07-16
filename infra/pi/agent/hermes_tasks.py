"""ACP-based hybrid delegation task manager (ADR-0022).

Owns the background Hermes tasks for one Agent Worker job/session. Each delegated
request drives ONE ACP session end-to-end over the long-lived ``hermes acp``
process (:class:`acp_client.AcpClient`): ``session/new`` → ``session/prompt`` →
consume the streamed tool events → the final result. There is one live
:class:`HermesTask` per request — the single source of truth for status ("как
там?"), the UI feed, and (in later tasks) fast-window racing, milestone
narration, synthetic-tool-turn reintegration, and bounded proactive delivery.

This module is the SKELETON of that manager (first of a serial chain). It
implements the state model, admission/queue, the lossy-channel UI feed,
``list_tasks`` / ``cancel`` / ``shutdown``, and a working :meth:`_run_task` that
drives an ACP session to completion and stores the result in live state. Two
hooks mark where later tasks plug in without changing this file's shape:

- :meth:`_on_tool_event` — called per streamed tool event (extended by the
  narration task to speak milestones).
- :meth:`_on_task_complete` — called once a task settles (extended by the
  reintegration + bounded-delivery tasks to speak the result and write a
  synthetic tool turn into ``chat_ctx``).

The manager is plain (no livekit decorators) so it is fully unit-testable; the
``@function_tool`` adapters in worker_tools.py are thin wrappers that fetch the
manager from ``context.session.userdata`` and call into it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import deque
from dataclasses import dataclass, field

from acp_client import AcpCancelled

log = logging.getLogger("agent")

UI_TOPIC = "voiceagent"  # LiveKit data topic the web UI subscribes to
UI_FULL_OUTPUT_CAP = 4000  # chars of full Hermes output sent to the UI (data-msg size guard)
# UI data is published lossy (decoupled from the transcript's reliable channel,
# issue #23), so a datagram can be dropped on the Tailscale path. Re-send each
# message after these delays (seconds, after the immediate first send) so the UI
# self-heals: task snapshots are re-read fresh each time and feed events carry an
# id the client dedups on.
_UI_RESEND_DELAYS = (0.5, 1.2)

DEFAULT_FAST_WINDOW_S = 8.0
DEFAULT_MAX_CONCURRENT = 3
DEFAULT_TASK_TIMEOUT = 300.0
DEFAULT_DELIVERY_FALLBACK_S = 15.0
DEFAULT_MAX_QUEUED = 5
DEFAULT_OUTPUT_LIMIT = 600  # chars of Hermes output handed to the LLM per result

# Keep at most this many settled (done/failed/cancelled) tasks in live state so a
# long session can still answer "как там та задача?" without unbounded growth.
_FINISHED_KEEP = 20

# Terminal states — a task in one of these is settled and its status must be read
# straight from `state` (never reported as "still running").
_FINISHED_STATES = ("done", "failed", "cancelled")

DIRECTIVE_QUEUED = (
    "Принято (id: {task_id}), но я уже занят другими задачами — поставил в очередь "
    "(queued). Скажи пользователю, что возьмёшься чуть позже, и продолжай разговор."
)
DIRECTIVE_FULL = (
    "Слишком много задач уже в работе и очередь полна. Попроси пользователя "
    "подождать, пока освободишься, и не запускай эту задачу сейчас."
)
DIRECTIVE_BACKGROUND = (
    "Запущено в фоне (id: {task_id}). Дай пользователю одну короткую "
    "фразу-подтверждение и продолжай разговор — результат придёт позже сам. "
    "НЕ перезапускай эту задачу, чтобы узнать статус — результат озвучится автоматически."
)
# Honest failure returned synchronously when a task fails WITHIN the fast window
# (the answer is delivered this turn, so there is no background hand-off). {error}
# carries Hermes's trimmed error text.
DIRECTIVE_SYNC_FAILED = (
    "Задача не удалась: {error}. Скажи пользователю честно, что не получилось — "
    "не выдумывай успех. Если стоит попробовать снова, спроси его."
)


@dataclass
class HermesTask:
    """Live state for one delegated Hermes task (the single source of truth).

    ``state`` walks ``queued`` → ``running`` → one of ``done`` / ``failed`` /
    ``cancelled``; status readers key off it directly. ``last_tool`` / ``steps``
    are updated from the ACP tool-event stream. ``first_result`` is resolved with
    the (trimmed) answer text the moment the task settles — the future the
    fast-window racer awaits (wired in a later task).
    """

    task_id: str
    request: str
    label: str
    state: str = "queued"
    last_tool: str | None = None
    steps: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    result: str | None = None
    delivered_synchronously: bool = False
    # Fast-window handshake (see delegate): ``awaiting_sync`` is True only while a
    # delegate() call is racing this task's ``first_result`` against the fast
    # window; ``completion_pending`` is set by _on_task_complete when the task
    # settles *during* that race, so the deferred side-effects run exactly once at
    # the single settle point (_maybe_run_completion) rather than twice.
    awaiting_sync: bool = False
    completion_pending: bool = False
    session_id: str | None = None
    first_result: "asyncio.Future[str] | None" = field(default=None, repr=False)
    runner: "asyncio.Task | None" = field(default=None, repr=False)

    @property
    def finished(self) -> bool:
        return self.state in _FINISHED_STATES

    def elapsed(self, now: float) -> float:
        """Seconds the task has run: wall time since start, frozen at finish."""
        if self.started_at is None:
            return 0.0
        end = self.finished_at if self.finished_at is not None else now
        return round(end - self.started_at, 1)


class HermesTaskManager:
    """Owns the background Hermes tasks for one Agent Worker job/session (ADR-0022)."""

    def __init__(
        self,
        acp_client,
        *,
        fast_window_s: float = DEFAULT_FAST_WINDOW_S,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        task_timeout_s: float = DEFAULT_TASK_TIMEOUT,
        delivery_fallback_s: float = DEFAULT_DELIVERY_FALLBACK_S,
        max_queued: int = DEFAULT_MAX_QUEUED,
        output_limit: int = DEFAULT_OUTPUT_LIMIT,
    ) -> None:
        self._acp = acp_client
        self.fast_window_s = fast_window_s
        self.max_concurrent = max_concurrent
        self.task_timeout_s = task_timeout_s
        self.delivery_fallback_s = delivery_fallback_s
        self.max_queued = max_queued
        self.output_limit = output_limit

        # Single source of truth: every task (queued, running, and recently
        # finished) lives here, keyed by task_id, in creation order.
        self._tasks: dict[str, HermesTask] = {}
        self._queue: deque[HermesTask] = deque()  # queued tasks, FIFO
        self._seq = 0  # task-id counter ("t1", "t2", …)

        self._session = None  # bound AgentSession (proactive delivery, later tasks)
        self._publish = None  # async (data: bytes) -> None, or None
        self._event_seq = 0  # monotonic id for feed events (client dedups repeats)
        self._ui_tasks: set[asyncio.Task] = set()  # in-flight UI (re)publish tasks
        self._closing = False
        # Set whenever nothing is running or queued (test/shutdown join point).
        self._idle = asyncio.Event()
        self._idle.set()

    # -- wiring -------------------------------------------------------------
    def attach_session(self, session) -> None:
        """Bind the live AgentSession used for proactive replies (later tasks)."""
        self._session = session

    def set_publisher(self, publish) -> None:
        """Bind an async ``publish(data: bytes)`` used to stream UI events to the
        browser (LiveKit data messages). None disables publishing (the default)."""
        self._publish = publish

    # -- public API ---------------------------------------------------------
    async def delegate(self, request: str) -> str:
        """Admit, queue, or refuse a Hermes task; return a directive for the LLM.

        Hybrid fast window (ADR-0022): an admitted task is started immediately and
        its final result raced against ``fast_window_s``. If Hermes answers within
        the window the reply is returned synchronously as the tool result (the LLM
        speaks it this turn); otherwise the task drops to the background and its
        ``task_id`` + a "continues in the background" directive is returned.

        A task that must be QUEUED (over capacity) does NOT consume a fast window —
        it returns the queued directive at once and, when later promoted, runs
        purely in the background.

        Fast-window ⇄ completion handshake (keeps the completion side-effects
        single-fire despite the race): the result may land — and ``_run_task`` may
        already have called ``_on_task_complete`` — *before* this coroutine wakes
        from ``wait_for``. So we mark ``task.awaiting_sync`` before awaiting; while
        it is set ``_on_task_complete`` only emits the UI "done"/"error" event and
        records ``completion_pending`` instead of running its post-completion hooks.
        Both exits below (win or timeout) clear ``awaiting_sync`` and drain any
        deferred completion via ``_maybe_run_completion`` — the single settle point
        later chain tasks extend for reintegration/delivery (skipped on a
        synchronous win, since the answer already went back as the tool result).
        """
        if self._running_count() >= self.max_concurrent:
            if len(self._queue) >= self.max_queued:
                return DIRECTIVE_FULL
            task = self._make_task(request)
            task.state = "queued"
            self._queue.append(task)
            self._emit_delegated(task)
            self._emit_tasks()
            return DIRECTIVE_QUEUED.format(task_id=task.task_id)

        task = self._start_task(request)
        self._emit_delegated(task)
        self._emit_tasks()

        task.awaiting_sync = True
        try:
            # SHIELD is required: a fast-window timeout must not cancel the
            # underlying future/task — the task keeps running in the background.
            text = await asyncio.wait_for(
                asyncio.shield(task.first_result), self.fast_window_s
            )
        except asyncio.TimeoutError:
            task.awaiting_sync = False
            # If the task settled right at the window boundary, drain its deferred
            # completion now; otherwise its own _on_task_complete runs the hooks.
            self._maybe_run_completion(task)
            return DIRECTIVE_BACKGROUND.format(task_id=task.task_id)

        # Result landed within the window → deliver synchronously as the tool result.
        task.delivered_synchronously = True
        task.awaiting_sync = False
        self._maybe_run_completion(task)
        if task.state == "failed":
            return DIRECTIVE_SYNC_FAILED.format(error=text)
        return text

    async def cancel(self, hint: str = "") -> str:
        """Cancel active Hermes tasks (running + queued); return a directive.

        Empty hint cancels everything; a hint matches a task by ``task_id`` or by
        a case-insensitive substring of its label. Cancelled tasks deliver no
        result and are kept in state as ``cancelled``.
        """
        hint_l = hint.strip().lower()

        def matches(task: HermesTask) -> bool:
            return (
                not hint_l
                or hint_l == task.task_id.lower()
                or hint_l in task.label.lower()
            )

        targets = [
            t
            for t in self._tasks.values()
            if t.state in ("running", "queued") and matches(t)
        ]
        if not targets:
            return "Сейчас нет активных задач для отмены. Так и скажи пользователю."

        for task in targets:
            await self._cancel_task(task)

        self._emit({"type": "event", "kind": "cancelled", "count": len(targets)})
        self._promote_queued()
        self._prune_finished()
        self._emit_tasks()
        self._update_idle()
        return (
            f"Отменил задач(и): {len(targets)}. Подтверди пользователю, что остановил "
            "их, и продолжай разговор."
        )

    async def shutdown(self) -> None:
        """Cancel every running task, drain the queue, close the ACP client.

        Silent (no proactive speech): the room is gone. Safe and idempotent —
        registered via ``ctx.add_shutdown_callback`` and may be called more than once.
        """
        self._closing = True

        for task in list(self._queue):
            task.state = "cancelled"
            task.finished_at = self._now()
        self._queue.clear()

        runners = [
            t.runner
            for t in self._tasks.values()
            if t.runner is not None and not t.runner.done()
        ]
        for runner in runners:
            runner.cancel()
        for ui_task in list(self._ui_tasks):
            ui_task.cancel()
        await asyncio.gather(*runners, *self._ui_tasks, return_exceptions=True)

        for task in self._tasks.values():
            if task.state == "running":
                task.state = "cancelled"
                task.finished_at = self._now()

        with contextlib.suppress(Exception):
            await self._acp.aclose()
        self._update_idle()

    def list_tasks(self) -> str:
        """Human-readable live status, read straight from task state.

        Never reports a finished task as "still running": running / queued /
        finished are partitioned by ``state``.
        """
        now = self._now()
        running = [t for t in self._tasks.values() if t.state == "running"]
        queued = [t for t in self._tasks.values() if t.state == "queued"]
        finished = [t for t in self._tasks.values() if t.finished]

        if not running and not queued and not finished:
            return "Сейчас никаких фоновых задач нет."

        parts: list[str] = []
        if running:
            items = []
            for t in running:
                desc = f"«{t.label}» ({t.elapsed(now):.0f}с, шагов: {t.steps}"
                if t.last_tool:
                    desc += f", сейчас: {t.last_tool}"
                desc += ")"
                items.append(desc)
            parts.append("в работе: " + "; ".join(items))
        if queued:
            parts.append("в очереди: " + "; ".join(f"«{t.label}»" for t in queued))
        if finished:
            state_ru = {"done": "готово", "failed": "ошибка", "cancelled": "отменено"}
            items = []
            for t in finished[-5:]:
                preview = self._trim(t.result, 80) if t.result else ""
                tail = f": {preview}" if preview else ""
                items.append(f"«{t.label}» [{state_ru[t.state]}]{tail}")
            parts.append("завершено: " + "; ".join(items))
        return ". ".join(parts) + "."

    async def join(self) -> None:
        """Test/shutdown helper: wait until nothing is running or queued."""
        await self._idle.wait()

    # -- internals: labelling / trimming -----------------------------------
    @staticmethod
    def _label(request: str) -> str:
        """A short human label for a request, for status/cancel/UI messages."""
        label = " ".join(request.split())
        return label if len(label) <= 48 else label[:47] + "…"

    def _trim(self, text: str | None, limit: int | None = None) -> str:
        """Collapse whitespace and cap length so TTS never chokes on a wall of text."""
        t = " ".join((text or "").split())
        cap = self.output_limit if limit is None else limit
        return t if len(t) <= cap else t[:cap] + "…"

    def _now(self) -> float:
        return asyncio.get_event_loop().time()

    # -- internals: scheduling ---------------------------------------------
    def _next_id(self) -> str:
        self._seq += 1
        return f"t{self._seq}"

    def _running_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.state == "running")

    def _make_task(self, request: str) -> HermesTask:
        """Create + register a task (still ``queued``) in the single source of truth."""
        task = HermesTask(
            task_id=self._next_id(), request=request, label=self._label(request)
        )
        self._tasks[task.task_id] = task
        return task

    def _start_task(self, request: str) -> HermesTask:
        """Create a task and launch it immediately as a running background task."""
        task = self._make_task(request)
        self._launch(task)
        return task

    def _launch(self, task: HermesTask) -> None:
        """Move a task into ``running`` and spawn its ACP driver."""
        task.state = "running"
        task.started_at = self._now()
        task.finished_at = None
        task.first_result = asyncio.get_event_loop().create_future()
        self._idle.clear()
        task.runner = asyncio.create_task(self._run_task(task))
        task.runner.add_done_callback(lambda _t: self._update_idle())

    def _promote_queued(self) -> None:
        """Fill freed slots from the FIFO queue."""
        while self._queue and self._running_count() < self.max_concurrent:
            self._launch(self._queue.popleft())

    def _prune_finished(self) -> None:
        """Keep at most ``_FINISHED_KEEP`` settled tasks; drop the oldest beyond that."""
        finished_ids = [tid for tid, t in self._tasks.items() if t.finished]
        for tid in finished_ids[:-_FINISHED_KEEP] if len(finished_ids) > _FINISHED_KEEP else []:
            self._tasks.pop(tid, None)

    async def _run_task(self, task: HermesTask) -> None:
        """Drive one ACP session end-to-end and store the result in live state.

        session/new → session/prompt → consume tool events (updating live state)
        → final result. On success the task settles ``done`` with the trimmed
        answer; on an ACP crash/error it settles ``failed`` with the error text.
        A local cancellation (``AcpCancelled`` or ``CancelledError``) is owned by
        :meth:`_cancel_task`, which sets ``cancelled`` — so it never settles here.
        """
        try:
            session_id = await self._acp.new_session()
            task.session_id = session_id
            handle = await self._acp.prompt(session_id, task.request)
            async for ev in handle.events:
                self._on_tool_event(task, ev)
            answer = await handle.result
            task.result = self._trim(answer)
            task.state = "done"
        except AcpCancelled:
            return  # cancellation is settled by _cancel_task
        except asyncio.CancelledError:
            raise  # cancellation is settled by _cancel_task
        except Exception as exc:  # noqa: BLE001 — surface any failure honestly
            task.result = self._trim(str(exc))
            task.state = "failed"
            log.warning("hermes task %s (%s) failed: %r", task.task_id, task.label, exc)

        task.finished_at = self._now()
        if task.first_result is not None and not task.first_result.done():
            task.first_result.set_result(task.result or "")
        self._on_task_complete(task)

    def _on_tool_event(self, task: HermesTask, ev) -> None:
        """Update live per-task state from one streamed ACP tool event.

        HOOK: the narration task extends this to speak key milestones via
        ``session.say`` when the channel is free. The ``tool_call_update`` (finish)
        edge carries no title, so ``last_tool`` keeps the last named tool.
        """
        if getattr(ev, "title", None):
            task.last_tool = ev.title
        task.steps += 1
        self._emit_tasks()

    def _on_task_complete(self, task: HermesTask) -> None:
        """Settle bookkeeping for a task that just reached a terminal state.

        The UI feed event is emitted immediately in BOTH paths (synchronous win
        and background) so the browser always sees the result once. The remaining
        post-completion side-effects are deferred while a fast-window race is still
        open (``awaiting_sync``): they run at the single settle point once
        ``delegate`` resolves the race (see the handshake note on :meth:`delegate`).
        """
        self._emit_completion_event(task)
        if task.awaiting_sync:
            # Fast window still open: defer the hooks to _maybe_run_completion so
            # they (and, later, reintegration/delivery) fire exactly once.
            task.completion_pending = True
            return
        self._settle_completion(task)

    def _emit_completion_event(self, task: HermesTask) -> None:
        """Publish the one-shot "done"/"error" UI feed entry for a settled task."""
        kind = "done" if task.state == "done" else "error"
        body = task.result or ""
        self._emit(
            {
                "type": "event",
                "kind": kind,
                "label": task.label,
                "task_id": task.task_id,
                "summary": self._trim(body),
                "full": body[:UI_FULL_OUTPUT_CAP],
            }
        )

    def _settle_completion(self, task: HermesTask) -> None:
        """Post-completion side-effects, run once the fast-window race is resolved.

        HOOK + single decision point: the reintegration + bounded-delivery tasks
        extend this to write a synthetic tool turn into ``chat_ctx`` and speak the
        result — SKIPPED for ``delivered_synchronously`` tasks (the answer already
        went back as the tool result). For now it promotes the next queued task,
        prunes memory, refreshes the snapshot, and updates the idle gate.
        """
        self._promote_queued()
        self._prune_finished()
        self._emit_tasks()
        self._update_idle()

    def _maybe_run_completion(self, task: HermesTask) -> None:
        """Drain a completion deferred while this task's fast window was open.

        A no-op unless ``_on_task_complete`` ran during the race (``completion_
        pending``): the timeout path's task may still be running (its own
        ``_on_task_complete`` runs the hooks later), and the win path already
        emitted the UI event — this just runs the deferred settle exactly once.
        """
        if not task.completion_pending:
            return
        task.completion_pending = False
        self._settle_completion(task)

    async def _cancel_task(self, task: HermesTask) -> None:
        """Cancel one task (queued or running); leave it in state ``cancelled``."""
        if task.state == "queued":
            with contextlib.suppress(ValueError):
                self._queue.remove(task)
            task.state = "cancelled"
            task.finished_at = self._now()
            return

        if task.session_id is not None:
            with contextlib.suppress(Exception):
                await self._acp.cancel_prompt(task.session_id)
        if task.runner is not None:
            task.runner.cancel()
            await asyncio.gather(task.runner, return_exceptions=True)
        # Only claim it as cancelled if it did not settle on its own meanwhile.
        if task.state == "running":
            task.state = "cancelled"
            task.finished_at = self._now()

    def _update_idle(self) -> None:
        """Set the idle event iff nothing is running or queued."""
        active = any(
            t.state in ("running", "queued") for t in self._tasks.values()
        )
        if active:
            self._idle.clear()
        else:
            self._idle.set()

    def _session_is_idle(self) -> bool:
        """Whether the conversation is quiet enough to speak a result into.

        Used by the proactive-delivery worker (later tasks) as the fallback idle
        probe when the framework's own ``wait_for_idle`` primitive is unavailable.
        """
        s = self._session
        if s is None:
            return True
        return (
            getattr(s, "agent_state", "listening") == "listening"
            and getattr(s, "user_state", "listening") != "speaking"
            and getattr(s, "current_speech", None) is None
        )

    # -- internals: UI publishing ------------------------------------------
    def _emit_delegated(self, task: HermesTask) -> None:
        self._emit(
            {
                "type": "event",
                "kind": "delegated",
                "label": task.label,
                "task_id": task.task_id,
            }
        )

    def _spawn(self, coro) -> None:
        """Run a fire-and-forget UI publish task, tracked so shutdown can cancel it."""
        task = asyncio.create_task(coro)
        self._ui_tasks.add(task)
        task.add_done_callback(self._ui_tasks.discard)

    def _emit(self, payload: dict) -> None:
        """Fire-and-forget publish a UI event (safe from sync or async context)."""
        if self._publish is None:
            return
        if payload.get("type") == "event":
            # Append-only feed entry (delegated/done/error/cancelled): give it a
            # stable id and send it a few times so a dropped lossy datagram does
            # not lose the result; the client renders each id exactly once.
            self._event_seq += 1
            payload["id"] = f"e{self._event_seq}"
            self._spawn(self._safe_publish(payload, sends=1 + len(_UI_RESEND_DELAYS)))
        else:
            self._spawn(self._safe_publish(payload, sends=1))

    async def _safe_publish(self, payload: dict, sends: int = 1) -> None:
        # UI data is published lossy (see agent.py set_publisher) so a dropped
        # message is expected and non-fatal; re-send `sends` times and log drops
        # at debug only.
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        for i in range(sends):
            if i:
                await asyncio.sleep(_UI_RESEND_DELAYS[i - 1])
            try:
                await self._publish(data)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.debug("UI publish dropped (%d bytes): %r", len(data), e)

    def _emit_tasks(self) -> None:
        """Publish the running + queued task snapshot now, then re-publish a couple
        of *fresh* snapshots after a short delay. The snapshot is idempotent and
        re-read each time, so a dropped lossy snapshot self-heals and a late
        re-send can never resurrect an already-finished task."""
        if self._publish is None:
            return
        self._publish_tasks_once()
        self._spawn(self._resend_tasks())

    def _publish_tasks_once(self) -> None:
        now = self._now()
        running = []
        for t in self._tasks.values():
            if t.state != "running":
                continue
            entry = {"label": t.label, "elapsed": t.elapsed(now)}
            if t.last_tool:
                entry["last_tool"] = t.last_tool  # optional (ops.js ignores unknown keys)
            running.append(entry)
        queued = [t.label for t in self._tasks.values() if t.state == "queued"]
        self._emit({"type": "tasks", "running": running, "queued": queued})

    async def _resend_tasks(self) -> None:
        for delay in _UI_RESEND_DELAYS:
            await asyncio.sleep(delay)
            if self._publish is None:
                return
            self._publish_tasks_once()
