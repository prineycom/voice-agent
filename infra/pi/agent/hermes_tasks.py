"""Async Hermes delegation manager.

Runs Hermes CLI tasks in the background so the Agent Worker stays conversational:
the delegating tool returns immediately, the agent voices a short acknowledgement,
and when Hermes finishes the manager makes the agent speak the result proactively
via ``session.generate_reply``. See docs/superpowers/specs/2026-06-25-hermes-async-delegation-design.md.

Proactive delivery is deliberately conservative to avoid flooding the framework's
speech queue (the failure mode that made the agent natter and garble audio):
- NO progress nudges — a long task stays silent until it actually finishes.
- Results are buffered and delivered by ONE worker that waits for the conversation
  to go idle (agent listening, user not speaking, no current speech), so a result
  never cuts off the user or the agent mid-utterance.
- Multiple results that pile up are coalesced into a SINGLE reply, and each task's
  raw output is trimmed before it reaches the LLM/TTS.

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

log = logging.getLogger("agent")

UI_TOPIC = "voiceagent"  # LiveKit data topic the web UI subscribes to
UI_FULL_OUTPUT_CAP = 4000  # chars of full Hermes output sent to the UI (data-msg size guard)
# chars of a completed background result injected into the agent's chat context
# so it can be referenced in later turns (larger than the TTS trim, bounded so a
# wall of Hermes output never bloats the prompt).
CONTEXT_RESULT_CAP = 2000
# UI data is published lossy (decoupled from the transcript's reliable channel,
# issue #23), so a datagram can be dropped on the Tailscale path. Re-send each
# message after these delays (seconds, after the immediate first send) so the UI
# self-heals: task snapshots are re-read fresh each time and feed events carry an
# id the client dedups on.
_UI_RESEND_DELAYS = (0.5, 1.2)

DEFAULT_MAX_CONCURRENT = 3
DEFAULT_MAX_QUEUED = 5
DEFAULT_TASK_TIMEOUT = 300.0
DEFAULT_IDLE_POLL_INTERVAL = 0.3
DEFAULT_OUTPUT_LIMIT = 600  # chars of Hermes output handed to the LLM per result

DIRECTIVE_BACKGROUND = (
    "Запущено в фоне (running in background). Дай пользователю одну короткую "
    "фразу-подтверждение и продолжай разговор — результат придёт позже сам. "
    "НЕ перезапускай эту задачу, чтобы узнать статус — результат озвучится автоматически."
)
DIRECTIVE_QUEUED = (
    "Принято, но я уже занят другими задачами — поставил в очередь (queued). "
    "Скажи пользователю, что возьмёшься чуть позже, и продолжай разговор."
)
DIRECTIVE_FULL = (
    "Слишком много задач уже в работе и очередь полна. Попроси пользователя "
    "подождать, пока освободишься, и не запускай эту задачу сейчас."
)


def build_hermes_argv(request: str, *, resume_session_id: str | None = None) -> list[str]:
    """Compose the Hermes CLI argv for a single delegated request.

    -Q (quiet: final answer + session_id only), --yolo (no approval prompts; no
    TTY in a subprocess), --source tool (mark as tool-originated). --resume keeps
    one continuous Hermes dialog across delegations.
    """
    argv = ["hermes", "chat", "-q", request, "-Q", "--yolo", "--source", "tool"]
    if resume_session_id:
        argv += ["--resume", resume_session_id]
    return argv


class HermesTaskManager:
    """Owns background Hermes tasks for one Agent Worker job/session."""

    def __init__(
        self,
        *,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        max_queued: int = DEFAULT_MAX_QUEUED,
        task_timeout: float = DEFAULT_TASK_TIMEOUT,
        idle_poll_interval: float = DEFAULT_IDLE_POLL_INTERVAL,
        output_limit: int = DEFAULT_OUTPUT_LIMIT,
    ) -> None:
        self.max_concurrent = max_concurrent
        self.max_queued = max_queued
        self.task_timeout = task_timeout
        self.idle_poll_interval = idle_poll_interval
        self.output_limit = output_limit

        self._session = None
        self._session_id: str | None = None
        self._publish = None  # async (data: bytes) -> None, or None
        self._tasks: set[asyncio.Task] = set()
        self._labels: dict[asyncio.Task, str] = {}
        self._started_at: dict[asyncio.Task, float] = {}
        self._queue: deque[str] = deque()
        self._running = 0
        # Completed results awaiting a quiet moment to be spoken: (label, ok, text).
        self._pending: list[tuple[str, bool, str]] = []
        self._delivery_task: asyncio.Task | None = None
        self._delivering = False
        self._event_seq = 0  # monotonic id for feed events (client dedups repeats)
        self._ui_tasks: set[asyncio.Task] = set()  # in-flight UI (re)publish tasks
        # Set whenever nothing is running, queued, pending, or being delivered.
        self._idle = asyncio.Event()
        self._idle.set()

    # -- wiring -------------------------------------------------------------
    def attach_session(self, session) -> None:
        """Bind the live AgentSession used for proactive replies."""
        self._session = session

    def set_publisher(self, publish) -> None:
        """Bind an async ``publish(data: bytes)`` used to stream UI events to the
        browser (LiveKit data messages). None disables publishing (the default)."""
        self._publish = publish

    # -- public API ---------------------------------------------------------
    async def delegate(self, request: str) -> str:
        """Admit, queue, or refuse a Hermes task; return a directive for the LLM."""
        self._idle.clear()
        self._emit({"type": "event", "kind": "delegated", "label": self._label(request)})
        if self._running < self.max_concurrent:
            self._start(request)
            self._emit_tasks()
            return DIRECTIVE_BACKGROUND
        if len(self._queue) < self.max_queued:
            self._queue.append(request)
            self._emit_tasks()
            return DIRECTIVE_QUEUED
        self._update_idle()
        return DIRECTIVE_FULL

    async def cancel(self, hint: str = "") -> str:
        """Cancel active Hermes tasks (running + queued); return a directive.

        Empty hint cancels everything; a hint cancels only tasks whose request
        label contains it (case-insensitive). Cancelled tasks deliver no result.
        """
        hint_l = hint.strip().lower()

        def matches(label: str) -> bool:
            return not hint_l or hint_l in label.lower()

        running_targets = [t for t in list(self._tasks) if matches(self._labels.get(t, ""))]
        queued_kept = deque(r for r in self._queue if not matches(self._label(r)))
        queued_cancelled = len(self._queue) - len(queued_kept)
        self._queue = queued_kept

        if not running_targets and queued_cancelled == 0:
            self._update_idle()
            return "Сейчас нет активных задач для отмены. Так и скажи пользователю."

        for task in running_targets:
            task.cancel()
        await asyncio.gather(*running_targets, return_exceptions=True)
        self._update_idle()

        total = len(running_targets) + queued_cancelled
        self._emit({"type": "event", "kind": "cancelled", "count": total})
        self._emit_tasks()
        return (
            f"Отменил задач(и): {total}. Подтверди пользователю, что остановил их, "
            "и продолжай разговор."
        )

    async def shutdown(self) -> None:
        """Cancel everything and kill subprocesses — called when the session ends.

        Silent (no proactive speech): the room is gone, there is no one to tell.
        """
        self._queue.clear()
        self._pending.clear()
        targets = list(self._tasks) + list(self._ui_tasks)
        if self._delivery_task is not None:
            targets.append(self._delivery_task)
        for task in targets:
            task.cancel()
        await asyncio.gather(*targets, return_exceptions=True)
        self._update_idle()

    def list_tasks(self) -> str:
        """A short summary of running and queued tasks for the agent to read out."""
        running = list(self._labels.values())
        queued = [self._label(r) for r in self._queue]
        if not running and not queued:
            return "Сейчас никаких фоновых задач нет."
        parts = []
        if running:
            parts.append("в работе: " + "; ".join(running))
        if queued:
            parts.append("в очереди: " + "; ".join(queued))
        return ". ".join(parts) + "."

    async def join(self) -> None:
        """Test/shutdown helper: wait until nothing is running, queued, or pending."""
        await self._idle.wait()

    # -- internals: scheduling ---------------------------------------------
    @staticmethod
    def _label(request: str) -> str:
        """A short human label for a request, for status/cancel messages."""
        label = " ".join(request.split())
        return label if len(label) <= 48 else label[:47] + "…"

    def _trim(self, text: str, limit: int | None = None) -> str:
        """Collapse whitespace and cap length so TTS never chokes on a wall of text."""
        t = " ".join(text.split())
        cap = self.output_limit if limit is None else limit
        return t if len(t) <= cap else t[:cap] + "…"

    def _start(self, request: str) -> None:
        """Admit a request: occupy a slot and spawn its background task."""
        self._running += 1
        task = asyncio.create_task(self._run(request))
        self._tasks.add(task)
        self._labels[task] = self._label(request)
        self._started_at[task] = asyncio.get_event_loop().time()
        task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task) -> None:
        """Free the slot and pull the next queued request, if any."""
        self._tasks.discard(task)
        self._labels.pop(task, None)
        self._started_at.pop(task, None)
        self._running -= 1
        if self._queue:
            self._start(self._queue.popleft())
        self._update_idle()
        self._emit_tasks()

    # -- internals: UI publishing ------------------------------------------
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
        now = asyncio.get_event_loop().time()
        running = [
            {"label": label, "elapsed": round(now - self._started_at.get(task, now), 1)}
            for task, label in self._labels.items()
        ]
        queued = [self._label(r) for r in self._queue]
        self._emit({"type": "tasks", "running": running, "queued": queued})

    async def _resend_tasks(self) -> None:
        for delay in _UI_RESEND_DELAYS:
            await asyncio.sleep(delay)
            if self._publish is None:
                return
            self._publish_tasks_once()

    def _update_idle(self) -> None:
        """Set the idle event iff there is no outstanding work of any kind."""
        if self._running == 0 and not self._queue and not self._pending and not self._delivering:
            self._idle.set()

    async def _run(self, request: str) -> None:
        label = self._label(request)
        argv = build_hermes_argv(request, resume_session_id=self._session_id)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            self._enqueue_result(label, False, "Hermes не найден на этом хосте")
            return

        comm = asyncio.create_task(proc.communicate())
        try:
            stdout_b, stderr_b = await asyncio.wait_for(comm, timeout=self.task_timeout)
        except asyncio.TimeoutError:
            proc.kill()
            comm.cancel()
            # await on a cancelled task raises CancelledError (a BaseException),
            # which suppress(Exception) would NOT catch — suppress everything so
            # cleanup never escapes before we enqueue the failure result.
            with contextlib.suppress(BaseException):
                await comm
            self._enqueue_result(label, False, "не уложилась в отведённое время — отменил")
            return
        except asyncio.CancelledError:
            proc.kill()
            comm.cancel()
            # await on a cancelled task raises CancelledError (a BaseException),
            # which suppress(Exception) would NOT catch — suppress everything so
            # cleanup never escapes before we enqueue the failure result.
            with contextlib.suppress(BaseException):
                await comm
            raise  # cancelled tasks deliver nothing

        stdout = stdout_b.decode("utf-8", errors="replace").strip()
        stderr = stderr_b.decode("utf-8", errors="replace").strip()
        for line in stderr.splitlines():
            if line.strip().startswith("session_id:"):
                self._session_id = line.split("session_id:", 1)[1].strip()

        if proc.returncode not in (0, None):
            self._enqueue_result(label, False, (stderr or stdout)[-self.output_limit:])
            return
        self._enqueue_result(label, True, stdout or "(no output)")

    # -- internals: delivery ------------------------------------------------
    def _enqueue_result(self, label: str, ok: bool, text: str) -> None:
        """Buffer a finished result and make sure the delivery worker is running."""
        self._idle.clear()
        self._emit({
            "type": "event",
            "kind": "done" if ok else "error",
            "label": label,
            "summary": self._trim(text),
            "full": text[:UI_FULL_OUTPUT_CAP],
        })
        self._pending.append((label, ok, text))
        if not self._delivering:
            self._delivering = True
            self._delivery_task = asyncio.create_task(self._delivery_worker())

    async def _delivery_worker(self) -> None:
        """Speak buffered results when the conversation is idle, coalesced into one
        reply per quiet moment so the speech queue never floods."""
        try:
            while self._pending:
                await self._wait_until_idle()
                batch = self._pending[:]
                self._pending.clear()
                if self._session is None:
                    return
                # Persist the raw result into the chat context BEFORE speaking, so
                # the agent can answer about it in later turns — and so it survives
                # even if the proactive generate_reply below fails (the result is no
                # longer carried only by the ephemeral `instructions=`). See #23 /
                # "agent loses background results": instructions are not added to history.
                await self._inject_results(batch)
                instructions = self._build_delivery(batch)
                handle = self._session.generate_reply(
                    instructions=instructions, allow_interruptions=True
                )
                try:
                    await handle
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.warning("hermes proactive delivery failed: %r", e)
        finally:
            # Drop the delivering flag, then re-arm if a result landed during the
            # final delivery (the window after the while-check) so it is not stranded.
            self._delivering = False
            if self._pending:
                self._delivering = True
                self._delivery_task = asyncio.create_task(self._delivery_worker())
            else:
                self._update_idle()

    async def _wait_until_idle(self) -> None:
        """Block until the agent is listening and the user is not speaking.

        Prefer the framework's own idle primitive (it knows about every speech
        source, not just the ones we poll); fall back to the state poll when it
        is unavailable or errors. A non-cancellation error from the primitive
        falls through to the state poll so idle-gating is still attempted; the
        worker's ``_session is None`` guard handles a session that has gone away.
        """
        if self._session is not None and hasattr(self._session, "wait_for_idle"):
            try:
                await self._session.wait_for_idle()
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.debug("wait_for_idle unavailable, falling back to poll: %r", e)
        while not self._session_is_idle():
            await asyncio.sleep(self.idle_poll_interval)

    def _session_is_idle(self) -> bool:
        s = self._session
        if s is None:
            return True
        return (
            getattr(s, "agent_state", "listening") == "listening"
            and getattr(s, "user_state", "listening") != "speaking"
            and getattr(s, "current_speech", None) is None
        )

    async def _inject_results(self, batch: list[tuple[str, bool, str]]) -> None:
        """Durably append each finished result to the agent's chat context.

        ``generate_reply(instructions=...)`` does NOT persist its instructions in
        history, so a background result delivered only that way is forgotten the
        moment the reply ends (the agent cannot answer "what did that task find?"
        later). Writing the result as a system message into the live chat context
        makes it durable and recallable across turns.
        """
        agent = getattr(self._session, "current_agent", None)
        if agent is None or not hasattr(agent, "update_chat_ctx"):
            return
        try:
            chat_ctx = agent.chat_ctx.copy()
            for label, ok, text in batch:
                status = "" if ok else " — ошибка"
                body = " ".join(text.split())[:CONTEXT_RESULT_CAP]
                chat_ctx.add_message(
                    role="system",
                    content=f"[Результат фоновой задачи «{label}»{status}]: {body}",
                )
            await agent.update_chat_ctx(chat_ctx)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("failed to inject hermes result into chat ctx: %r", e)

    def _build_delivery(self, batch: list[tuple[str, bool, str]]) -> str:
        """Build ONE generate_reply instruction for a batch of finished results."""
        if len(batch) == 1:
            label, ok, text = batch[0]
            if ok:
                return (
                    f"Фоновая задача «{label}» готова. Результат Hermes: {self._trim(text)}. "
                    "Передай пользователю суть кратко, своими словами, без markdown."
                )
            return (
                f"Фоновая задача «{label}» завершилась с ошибкой: {self._trim(text, 200)}. "
                "Скажи пользователю кратко, что не получилось."
            )
        lines = []
        for label, ok, text in batch:
            status = "готово" if ok else "ошибка"
            lines.append(f"- «{label}» [{status}]: {self._trim(text, 200)}")
        joined = "\n".join(lines)
        return (
            "Готовы несколько фоновых задач. Сведи их в одну короткую устную сводку "
            f"для пользователя, без markdown:\n{joined}"
        )
