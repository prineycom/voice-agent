"""ACP-based hybrid delegation task manager (ADR-0022).

Owns the background Hermes tasks for one Agent Worker job/session. Each delegated
request drives ONE ACP session end-to-end over the long-lived ``hermes acp``
process (:class:`acp_client.AcpClient`): ``session/new`` → ``session/prompt`` →
consume the streamed tool events → the final result. There is one live
:class:`HermesTask` per request — the single source of truth for status ("как
там?"), the UI feed, and (in later tasks) fast-window racing, milestone
narration, synthetic-tool-turn reintegration, and bounded proactive delivery.

The manager implements the state model, admission/queue, the lossy-channel UI
feed, ``list_tasks`` / ``cancel`` / ``shutdown``, the :meth:`_run_task` ACP
driver, the fast-window race in :meth:`delegate`, milestone narration
(:meth:`_maybe_narrate`), synthetic-tool-turn reintegration
(:meth:`_reintegrate`), and bounded proactive delivery (:meth:`_deliver`).

Failure doctrine (ADR-0022): EVERY failure mode — ACP error, process crash,
the hard ``task_timeout_s`` cap — settles the task ``failed`` with an honest
result (error + last streamed step) and flows through the same reintegration
(``is_error=True``) + delivery path as success. NO AUTO-RETRY, ever: a failed
task is reported and left settled; retrying is the user's call. Hermes being
down is never a crash — the agent keeps talking.

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

from livekit.agents.llm import FunctionCall, FunctionCallOutput

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

# How long shutdown waits for in-flight finalizers (reintegration/delivery) to
# land their chat_ctx records before giving up on them (with a loud warning).
_FINALIZE_DRAIN_TIMEOUT_S = 2.0

# -- Milestone narration (ADR-0022 "Progress") -------------------------------
# Short template phrases spoken via ``session.say`` on a tool-call START edge —
# no LLM call, never written to chat_ctx, skipped (not queued) when the channel
# is busy. Keyed on the ACP tool ``kind``, refined first by the tool name parsed
# from the event ``title`` ("terminal: uname -a" → "terminal"). Keep phrases
# SHORT — they are spoken while the user waits.
NARRATION_MIN_GAP_S = 8.0  # at most one narration per task per this many seconds
NARRATION_BY_TOOL = {
    "terminal": "секунду, выполняю команду…",
}
NARRATION_BY_KIND = {
    "execute": "секунду, выполняю команду…",
    "read": "смотрю файлы…",
    "edit": "правлю файлы…",
    "fetch": "ищу информацию…",
    "search": "ищу информацию…",
    "think": "так, думаю…",
}
NARRATION_DEFAULT = "работаю над задачей «{label}»…"

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
# Returned when the task was cancelled while its own fast window was still open
# (e.g. the user asked to stop it mid-race): no result is coming, so promising a
# later automatic delivery would be a lie.
DIRECTIVE_SYNC_CANCELLED = (
    "Задачу (id: {task_id}) отменили — результата не будет. Подтверди пользователю "
    "отмену и продолжай разговор."
)

# -- Bounded delivery (ADR-0022 "Delivery of the spoken report") --------------
# generate_reply instructions for the proactive background report. The result /
# error is ALREADY in chat_ctx as the task_result tool turn (reintegration runs
# first), so the instructions only tell the model to voice it — tone per
# skills/hermes.md: short, conversational, no markdown, honest about failures.
DELIVERY_INSTRUCTIONS_DONE = (
    "Фоновая задача «{label}» (id: {task_id}) завершилась — её результат уже в "
    "контексте как результат инструмента task_result. Озвучь его пользователю "
    "сейчас: коротко, разговорно, без markdown."
)
DELIVERY_INSTRUCTIONS_FAILED = (
    "Фоновая задача «{label}» (id: {task_id}) НЕ удалась — ошибка уже в контексте "
    "как результат инструмента task_result (is_error). Честно скажи пользователю, "
    "что не получилось и почему. Не выдумывай успех и не повторяй запрос молча."
)
# Prepended when the fallback timer won the idle race (soft barge-in, ADR-0022).
DELIVERY_BARGE_IN_PREFIX = (
    "Разговор сейчас занят, поэтому вклинься мягко — начни со слов "
    "«кстати, по той задаче…». "
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
    # True once the result has been written into chat_ctx as a synthetic tool
    # turn (_reintegrate) — "the LLM can read this from context now"; delivery
    # runs strictly after this, so a delivery miss can never lose the answer.
    reintegrated: bool = False
    # True once the spoken background report was fired (_deliver's
    # generate_reply call succeeded) — "отчитался".
    delivered: bool = False
    # Fast-window handshake (see delegate): ``awaiting_sync`` is True only while a
    # delegate() call is racing this task's ``first_result`` against the fast
    # window; ``completion_pending`` is set by _on_task_complete when the task
    # settles *during* that race, so the deferred side-effects run exactly once at
    # the single settle point (_maybe_run_completion) rather than twice.
    awaiting_sync: bool = False
    completion_pending: bool = False
    # Milestone-narration throttle: the last tool kind spoken for this task (no
    # same-milestone repeats back-to-back) and when (rate limit, NARRATION_MIN_GAP_S).
    last_narrated_kind: str | None = None
    last_narrated_at: float | None = None
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
        # Signature of the last-published task snapshot (running task_ids + their
        # last_tool, plus queued task_ids). A tool-heavy task streams ~90 ACP
        # events, each calling _emit_tasks; most don't change what the UI renders,
        # so we skip republishing (and its two lossy re-sends) when unchanged.
        self._last_tasks_sig: tuple | None = None
        self._ui_tasks: set[asyncio.Task] = set()  # in-flight UI (re)publish tasks
        # In-flight _finalize (reintegrate → deliver) tasks, tracked SEPARATELY
        # from UI publishes: shutdown cancels UI tasks outright but must NOT
        # cancel these — a cancelled finalizer silently loses a finished task's
        # context record. Shutdown drains them with a short timeout instead.
        self._finalize_tasks: set[asyncio.Task] = set()
        # Serializes every read-modify-write of the agent's chat_ctx. The real
        # AgentActivity.update_chat_ctx REPLACES the context (no merge), so two
        # finalizers interleaving around the await would have the second's stale
        # copy() overwrite — and silently drop — the first's tool-turn pair.
        # Any future chat_ctx read-modify-write must reuse this lock discipline.
        self._chat_ctx_lock = asyncio.Lock()
        # Serializes spoken deliveries so two near-simultaneous background
        # reports don't fight over the channel: the second delivery (including
        # its own idle race) starts only after the first's generate_reply CALL.
        self._delivery_lock = asyncio.Lock()
        self._closing = False
        # Set whenever nothing is running or queued (test/shutdown join point).
        self._idle = asyncio.Event()
        self._idle.set()

    # -- wiring -------------------------------------------------------------
    def attach_session(self, session) -> None:
        """Bind the live AgentSession used for narration and proactive delivery."""
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
        where reintegration/delivery are decided (skipped on a synchronous win,
        since the answer already went back as the real tool result).
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
        log.info(
            "delegate: task %s admitted (%s)", task.task_id, self._trim(request, 80)
        )
        self._emit_delegated(task)
        self._emit_tasks()

        task.awaiting_sync = True
        try:
            try:
                # SHIELD is required: a fast-window timeout must not cancel the
                # underlying future/task — the task keeps running in the background.
                text = await asyncio.wait_for(
                    asyncio.shield(task.first_result), self.fast_window_s
                )
            except asyncio.TimeoutError:
                if task.state == "cancelled":
                    # Cancelled while the window was open: no result is coming, so
                    # the background promise would be a lie.
                    return DIRECTIVE_SYNC_CANCELLED.format(task_id=task.task_id)
                log.info(
                    "delegate: task %s (%s) → BACKGROUND (fast window elapsed)",
                    task.task_id,
                    task.label,
                )
                return DIRECTIVE_BACKGROUND.format(task_id=task.task_id)

            if task.state == "cancelled":
                # _cancel_task released first_result to wake this racer early;
                # that empty resolution is a wake-up, not an answer.
                return DIRECTIVE_SYNC_CANCELLED.format(task_id=task.task_id)

            # Result landed within the window → deliver it as the tool result.
            task.delivered_synchronously = True
            if task.state == "failed":
                log.info("delegate: task %s (%s) → SYNC FAILED", task.task_id, task.label)
                return DIRECTIVE_SYNC_FAILED.format(error=text)
            log.info(
                "delegate: task %s (%s) → SYNC WIN (%d chars to LLM)",
                task.task_id,
                task.label,
                len(text or ""),
            )
            return text
        finally:
            # ALWAYS close the window — win, timeout, delegate() itself being
            # cancelled (the framework cancels in-flight tool calls on barge-in),
            # or any other exception. Otherwise ``awaiting_sync`` stays True
            # forever and _on_task_complete defers into ``completion_pending``
            # that nothing ever drains (queued tasks would starve). A pending
            # CancelledError re-raises after this cleanup.
            task.awaiting_sync = False
            self._maybe_run_completion(task)

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
            # Cancellation settles here (never via _on_task_complete), so write
            # its minimal context record now — a cancelled task with no record
            # would look un-run to the LLM and invite a silent re-delegation.
            self._spawn_finalize(self._finalize(task))

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

        # Finalizers are NOT cancelled: a task that genuinely finished right
        # before shutdown deserves its chat_ctx record, and cancelling would
        # drop it with no log (CancelledError bypasses _reintegrate's except).
        # Drain them briefly; anything still unfinished is logged loudly.
        if self._finalize_tasks:
            _done, pending = await asyncio.wait(
                list(self._finalize_tasks), timeout=_FINALIZE_DRAIN_TIMEOUT_S
            )
            if pending:
                log.warning(
                    "shutdown: %d finalizer(s) did not finish within %.1fs — "
                    "context record(s) may be lost",
                    len(pending),
                    _FINALIZE_DRAIN_TIMEOUT_S,
                )
                for p in pending:
                    p.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

        for task in self._tasks.values():
            if task.state == "running":
                task.state = "cancelled"
                task.finished_at = self._now()
            self._release_first_result(task)

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
            # If this runner is torn down mid-stream (cancel/shutdown), the
            # handle's result future can settle with AcpCancelled after we have
            # stopped awaiting it; consume the exception so asyncio never logs
            # "Future exception was never retrieved". Awaiting it below still
            # works — retrieval is idempotent.
            handle.result.add_done_callback(
                lambda f: None if f.cancelled() else f.exception()
            )
            # Hard per-task cap (ADR-0022, 300s default): ONE deadline bounds the
            # whole prompt consumption — the event stream AND the final result.
            answer = await asyncio.wait_for(
                self._consume_prompt(task, handle), self.task_timeout_s
            )
            task.result = self._trim(answer)
            task.state = "done"
        except AcpCancelled:
            return  # cancellation is settled by _cancel_task
        except asyncio.CancelledError:
            raise  # cancellation is settled by _cancel_task
        except asyncio.TimeoutError:
            # Must precede `except Exception` (TimeoutError is an Exception).
            # Don't leak the ACP-side work: best-effort cancel of the prompt.
            if task.session_id is not None:
                with contextlib.suppress(Exception):
                    await self._acp.cancel_prompt(task.session_id)
            task.result = self._trim(
                f"превышен лимит времени ({int(self.task_timeout_s)} с); "
                f"последний шаг: {task.last_tool or '—'}"
            )
            task.state = "failed"
            log.warning(
                "hermes task %s (%s) timed out after %.0fs (last step: %s)",
                task.task_id,
                task.label,
                self.task_timeout_s,
                task.last_tool,
            )
        except Exception as exc:  # noqa: BLE001 — surface any failure honestly
            detail = str(exc) or exc.__class__.__name__
            if task.last_tool:
                # The last streamed step gives the LLM/user something concrete
                # ("failed while doing X"), per ADR-0022 failure reporting.
                detail += f"; последний шаг: {task.last_tool}"
            task.result = self._trim(detail)
            task.state = "failed"
            log.warning("hermes task %s (%s) failed: %r", task.task_id, task.label, exc)

        # Every failure mode above (ACP error, crash, timeout) converges here —
        # the same completion path as success: release the fast-window future,
        # then reintegrate (is_error) + deliver via _on_task_complete.
        task.finished_at = self._now()
        if task.first_result is not None and not task.first_result.done():
            task.first_result.set_result(task.result or "")
        self._on_task_complete(task)

    async def _consume_prompt(self, task: HermesTask, handle) -> str:
        """Consume one prompt end-to-end: stream tool events, return the answer.

        Split out so a single ``wait_for`` deadline (``task_timeout_s``) bounds
        BOTH awaits — the event iterator and the final result future.
        """
        async for ev in handle.events:
            self._on_tool_event(task, ev)
        return await handle.result

    def _on_tool_event(self, task: HermesTask, ev) -> None:
        """Update live per-task state from one streamed ACP tool event.

        Live state FIRST — ``last_tool`` / ``steps`` feed ``list_tasks`` / "как
        там?" and must update on every event, whether or not anything is spoken.
        The ``tool_call_update`` (finish) edge carries no title, so ``last_tool``
        keeps the last named tool. Narration is a best-effort afterthought.
        """
        if getattr(ev, "title", None):
            task.last_tool = ev.title
        task.steps += 1
        self._emit_tasks()
        self._maybe_narrate(task, ev)

    def _maybe_narrate(self, task: HermesTask, ev) -> None:
        """Speak a short milestone phrase for a tool-call START edge (ADR-0022).

        Ephemeral template speech via ``session.say`` — no LLM call, and
        ``add_to_chat_ctx=False`` so it never enters the conversation context.
        Strictly bounded: only the ``tool_call`` START edge (never the
        ``tool_call_update`` completion edge), only when the conversation channel
        is free, never the same tool kind twice in a row, and at most one phrase
        per task per ``NARRATION_MIN_GAP_S``. A narration that cannot be spoken
        right now is SKIPPED, never queued — live state already captured the step.
        """
        if getattr(ev, "kind_of_update", None) != "tool_call":
            return  # completion edges are progress data, not milestones
        if getattr(ev, "status", None) not in (None, "pending", "in_progress"):
            return  # not a START edge
        # While this task's fast window is open the user is silently waiting for
        # the synchronous answer of THIS delegation — narrating mid-window would
        # talk over that wait, so milestones stay quiet until the race resolves.
        if task.awaiting_sync:
            return
        if self._session is None or not self._session_is_idle():
            return  # channel busy: skip, never queue
        kind = getattr(ev, "kind", None) or ""
        if kind and kind == task.last_narrated_kind:
            return  # same milestone as last time — don't repeat it
        now = self._now()
        if (
            task.last_narrated_at is not None
            and now - task.last_narrated_at < NARRATION_MIN_GAP_S
        ):
            return  # rate limit: tool-heavy tasks must not chatter

        # Phrase: tool-name refinement first ("terminal: uname -a" → "terminal"),
        # then the ACP kind, then the generic fallback.
        title = getattr(ev, "title", None) or ""
        tool = title.split(":", 1)[0].strip().lower() if ":" in title else ""
        phrase = (
            NARRATION_BY_TOOL.get(tool)
            or NARRATION_BY_KIND.get(kind)
            or NARRATION_DEFAULT.format(label=task.label)
        )
        try:
            # Fire-and-forget: say() is synchronous and returns a SpeechHandle;
            # we deliberately do not await its playout.
            self._session.say(phrase, add_to_chat_ctx=False, allow_interruptions=True)
        except Exception as e:  # noqa: BLE001 — narration must never break the task loop
            log.debug("milestone narration skipped: %r", e)
            return
        task.last_narrated_kind = kind
        task.last_narrated_at = now

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

        Single decision point: a BACKGROUND result (not ``delivered_synchronously``)
        is finalized — reintegrated into ``chat_ctx`` immediately and always, then
        handed to the ``_deliver`` hook. A synchronous win is skipped entirely: the
        fast window already returned the answer as the REAL tool result, so a
        synthetic copy would duplicate it. Scheduling bookkeeping (promote / prune /
        snapshot / idle gate) runs in both cases.
        """
        if not task.delivered_synchronously and task.state in ("done", "failed"):
            # async (update_chat_ctx awaits) → spawned; _finalize keeps the
            # required order: reintegration BEFORE any delivery logic.
            self._spawn_finalize(self._finalize(task))
        self._promote_queued()
        self._prune_finished()
        self._emit_tasks()
        self._update_idle()

    async def _finalize(self, task: HermesTask) -> None:
        """Reintegrate a background result into context, then hand off to delivery.

        The order is a hard invariant (ADR-0022): the context record must exist
        before any spoken report, so the LLM reads the authoritative tool result
        instead of re-delegating.
        """
        log.info(
            "finalize: task %s (%s) state=%s result=%d chars → reintegrate+deliver",
            task.task_id,
            task.label,
            task.state,
            len(task.result or ""),
        )
        await self._reintegrate(task)
        await self._deliver(task)

    async def _reintegrate(self, task: HermesTask) -> None:
        """Write the result into ``chat_ctx`` as a synthetic ``task_result`` tool turn.

        The ADR-0022 fix for the root-cause 0007 bug: a background answer glued in
        as a ``role="system"`` note was never treated as *the* answer, so the model
        re-delegated. Instead we append a paired ``FunctionCall`` +
        ``FunctionCallOutput`` (same ``call_id``) — an authoritative, tool-linked
        record. Runs immediately and always on background completion; failures
        reuse the same shape with ``is_error=True`` (honest report, ADR-0022).
        Best-effort: a reintegration failure must never crash the manager, but it
        is logged loudly — it means the result exists only as live task state.
        """
        agent = getattr(self._session, "current_agent", None)
        if agent is None:
            log.warning(
                "hermes task %s (%s): no agent attached — result NOT written to "
                "chat_ctx (kept in live task state only)",
                task.task_id,
                task.label,
            )
            return
        call_id = f"hermes_task_{task.task_id}"
        if task.state == "cancelled":
            # User-initiated cancel: a minimal context record is still useful (the
            # LLM should know the task ended without a result and not re-delegate
            # it silently), marked is_error so it is never read as an answer.
            output = "задача отменена, результата нет"
        else:
            output = task.result or ""
        # Build the synthetic tool turn OUTSIDE the lock so the serialized
        # critical section below is as tight as possible (one fresh read + one
        # replace, no other work between them).
        turn = [
            FunctionCall(
                call_id=call_id,
                name="task_result",
                arguments=json.dumps(
                    {"task_id": task.task_id, "request": task.label},
                    ensure_ascii=False,
                ),
            ),
            FunctionCallOutput(
                call_id=call_id,
                name="task_result",
                output=output,
                is_error=task.state != "done",
            ),
        ]
        try:
            # The read-modify-write is serialized against other finalizers:
            # update_chat_ctx REPLACES the context (no merge), so the copy() MUST
            # be taken inside the lock or a concurrent finalizer's stale copy would
            # overwrite this pair. We read the FRESHEST agent.chat_ctx here, right
            # before the replace, to keep the copy→update gap to that single await.
            #
            # exclude_invalid_function_calls MUST be False: it defaults to True,
            # which makes update_chat_ctx DROP any FunctionCall/FunctionCallOutput
            # whose name is not a registered agent tool. `task_result` is a
            # synthetic tool name (never registered), so the default silently
            # strips this whole pair — the result then never reaches chat_ctx and
            # the LLM re-delegates. Keeping it False persists the synthetic turn.
            #
            # Residual race (needs live-Pi confirmation of livekit-agents
            # semantics): this lock serializes finalizers against each OTHER, not
            # against the framework's own chat_ctx writes. A user/assistant item
            # committed by the framework during the update_chat_ctx await could
            # still be clobbered by this stale-plus-turn copy. Shrinking the window
            # is the conservative mitigation; the real fix is an in-place
            # insert/append that persists without a full replace — switch to it
            # once a livekit-agents version is confirmed to expose one.
            async with self._chat_ctx_lock:
                chat_ctx = agent.chat_ctx.copy()
                before = len(chat_ctx.items)
                chat_ctx.insert(turn)
                await agent.update_chat_ctx(
                    chat_ctx, exclude_invalid_function_calls=False
                )
                after = len(agent.chat_ctx.items)
        except Exception:  # noqa: BLE001 — must not crash the manager
            log.exception(
                "hermes task %s (%s): reintegration failed — result NOT recorded "
                "in chat_ctx",
                task.task_id,
                task.label,
            )
            return
        task.reintegrated = True
        log.info(
            "reintegrate: task %s (%s) task_result written to chat_ctx "
            "(items %d→%d, output=%d chars, is_error=%s)",
            task.task_id,
            task.label,
            before,
            after,
            len(output),
            task.state != "done",
        )

    async def _deliver(self, task: HermesTask) -> None:
        """Speak the finished background task's report (ADR-0022 bounded delivery).

        Window + fallback: wait for a natural pause (``session.wait_for_idle``)
        up to ``delivery_fallback_s``, then deliver anyway with a soft barge-in
        ("кстати, по той задаче…"). This replaces the old unbounded idle gate
        that could sit on a finished result forever. Runs strictly AFTER
        :meth:`_reintegrate` (see :meth:`_finalize`), so the result is already
        safe in chat_ctx — a delivery failure loses only the spoken report,
        never the answer. No ``_chat_ctx_lock`` here: nothing below touches
        chat_ctx; generate_reply reads the (already updated) context itself.
        """
        if task.delivered_synchronously:
            return  # guarded upstream (_settle_completion); belt-and-braces
        if task.state == "cancelled":
            # cancel() already returned a spoken confirmation as its tool result;
            # a proactive "report" about a task the user just killed is noise.
            return
        if self._session is None:
            log.info(
                "hermes task %s (%s): no session attached — report not spoken "
                "(result is in chat_ctx/live state)",
                task.task_id,
                task.label,
            )
            return
        if self._closing:
            # Shutdown in progress: the room is gone, so a spoken report is
            # pointless — and _wait_for_pause would otherwise block up to
            # delivery_fallback_s (~15s) waiting for an idle that never comes,
            # tripping shutdown's short finalizer-drain timeout and logging a
            # misleading "context record(s) may be lost" warning. Reintegration
            # already ran (the answer is safe in chat_ctx); skip the now-useless
            # report so the finalizer completes fast.
            return

        # Serialize deliveries so two near-simultaneous reports don't fight over
        # the channel: the second waits, then runs its OWN idle race. The lock
        # covers only the idle race + the generate_reply CALL (not the playout);
        # the framework queues the actual speech, which is acceptable ordering.
        async with self._delivery_lock:
            barged_in = await self._wait_for_pause()
            # The pause wait can block up to delivery_fallback_s (~15s); the user
            # may disconnect in that window, stopping the session. generate_reply
            # then raises RuntimeError("AgentSession isn't running") — expected on
            # teardown, not an error (the answer is already safe in chat_ctx).
            # Re-check the session's activity (the same gate generate_reply uses)
            # and skip quietly instead of logging a scary traceback. Guard on
            # hasattr so a stand-in session without the attribute (tests) is
            # treated as running — only a REAL session that went None is skipped.
            if hasattr(self._session, "_activity") and self._session._activity is None:
                log.info(
                    "hermes task %s (%s): session stopped before delivery — "
                    "report not spoken (result is safe in chat_ctx)",
                    task.task_id,
                    task.label,
                )
                return
            template = (
                DELIVERY_INSTRUCTIONS_DONE
                if task.state == "done"
                else DELIVERY_INSTRUCTIONS_FAILED
            )
            instructions = (
                DELIVERY_BARGE_IN_PREFIX if barged_in else ""
            ) + template.format(label=task.label, task_id=task.task_id)
            try:
                # Synchronous call returning a SpeechHandle; fire the reply, do
                # not await playout.
                self._session.generate_reply(
                    instructions=instructions, allow_interruptions=True
                )
            except Exception:  # noqa: BLE001 — must not crash the manager
                log.exception(
                    "hermes task %s (%s): delivery failed — report not spoken "
                    "(result is already safe in chat_ctx)",
                    task.task_id,
                    task.label,
                )
                return
            task.delivered = True
            log.info(
                "deliver: task %s (%s) spoken report fired (barge_in=%s, "
                "reintegrated=%s)",
                task.task_id,
                task.label,
                barged_in,
                task.reintegrated,
            )

    async def _wait_for_pause(self) -> bool:
        """Race a natural conversation pause against ``delivery_fallback_s``.

        Returns True when the fallback timer won (the report will soft barge-in).
        Prefers the framework's ``session.wait_for_idle`` coroutine; falls back
        to polling :meth:`_session_is_idle` when the session doesn't expose it.
        If the idle wait *raises* (e.g. ActivityClosedError while the session is
        closing) we proceed as a barge-in — generate_reply will then fail loudly
        in the caller if the session is truly gone.
        """
        wait_idle = getattr(self._session, "wait_for_idle", None)
        if wait_idle is not None:
            idle_task = asyncio.create_task(wait_idle())
        else:
            idle_task = asyncio.create_task(self._poll_session_idle())
        timer_task = asyncio.create_task(asyncio.sleep(self.delivery_fallback_s))
        done, pending = await asyncio.wait(
            {idle_task, timer_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for p in pending:
            p.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if idle_task in done and idle_task.exception() is not None:
            log.debug("wait_for_idle failed: %r", idle_task.exception())
            return True
        return timer_task in done

    async def _poll_session_idle(self) -> None:
        """Fallback pause probe for sessions without ``wait_for_idle``."""
        while not self._session_is_idle():
            await asyncio.sleep(0.1)

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
        self._release_first_result(task)

    @staticmethod
    def _release_first_result(task: HermesTask) -> None:
        """Wake any fast-window racer on a task that will never produce a result.

        Cancellation/shutdown leaves ``first_result`` pending; a delegate() still
        racing it would sit out the full window and then promise a background
        result that never comes. Resolve it with an empty string — the racer reads
        ``task.state`` ("cancelled") and answers with the cancelled ack instead of
        treating the empty wake-up as an answer. No-op once resolved.
        """
        if task.first_result is not None and not task.first_result.done():
            task.first_result.set_result("")

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

        Used by milestone narration and, via :meth:`_poll_session_idle`, as the
        delivery pause probe when the framework's own ``wait_for_idle`` primitive
        is unavailable on the bound session.
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

    def _spawn_finalize(self, coro) -> None:
        """Run a _finalize (reintegrate → deliver) task, tracked in its own set.

        Deliberately NOT in ``_ui_tasks``: shutdown cancels UI publishes outright,
        but cancelling a just-spawned finalizer would drop a genuinely finished
        task's chat_ctx record with no log (CancelledError bypasses the finalizer's
        own error handling). Shutdown drains this set with a timeout instead.
        """
        task = asyncio.create_task(coro)
        self._finalize_tasks.add(task)
        task.add_done_callback(self._finalize_tasks.discard)

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

    def _tasks_signature(self) -> tuple:
        """Identity of the currently *renderable* snapshot: which tasks are
        running (and their ``last_tool``, the only per-event field the UI shows)
        plus which are queued. Deliberately excludes ``elapsed`` (it ticks every
        call) and ``steps`` (not published) so a stream of same-state tool events
        collapses to a single publish. Any real change — a new tool, a task
        settling, a promotion — shifts this tuple and re-emits immediately."""
        running = tuple(
            (t.task_id, t.last_tool)
            for t in self._tasks.values()
            if t.state == "running"
        )
        queued = tuple(
            t.task_id for t in self._tasks.values() if t.state == "queued"
        )
        return (running, queued)

    def _emit_tasks(self) -> None:
        """Publish the running + queued task snapshot now, then re-publish a couple
        of *fresh* snapshots after a short delay. The snapshot is idempotent and
        re-read each time, so a dropped lossy snapshot self-heals and a late
        re-send can never resurrect an already-finished task.

        Coalesced: identical back-to-back snapshots are suppressed (see
        _tasks_signature) so a tool-heavy task doesn't flood the lossy Pi/Tailscale
        path with hundreds of redundant datagrams. The lossy-datagram redundancy
        (immediate + two delayed re-sends) is preserved for every *real* change."""
        if self._publish is None:
            return
        sig = self._tasks_signature()
        if sig == self._last_tasks_sig:
            return  # unchanged since the last publish — suppress the duplicate emit
        self._last_tasks_sig = sig
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
