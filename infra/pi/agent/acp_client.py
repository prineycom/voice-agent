"""ACP (Agent Client Protocol) ndjson JSON-RPC transport for the long-lived
``hermes acp`` process, per docs/adr/0022-hermes-acp-hybrid-delegation.md.

This module provides two layers:

- :class:`AcpConnection`, a request/response + notification codec over
  newline-delimited JSON (ndjson) JSON-RPC 2.0. It is deliberately decoupled from
  any subprocess: it takes an ``asyncio`` stream reader/writer pair, so the
  subprocess supervisor can spawn ``hermes acp`` and compose this codec over its
  stdio without the codec knowing about processes.
- :class:`AcpClient`, the long-lived ``hermes acp`` process supervisor: it spawns
  the process, runs the ``initialize`` handshake, detects crashes, respawns
  on demand, and fails in-flight work honestly. Per-session routing
  (``session/new`` / ``session/prompt``) is layered on top in a later task via the
  live :class:`AcpConnection` that :meth:`AcpClient._ensure` returns.

Wire shape (JSON-RPC 2.0, one object per line):
- outbound request      → ``{"jsonrpc":"2.0","id":N,"method":..,"params":..}``
- inbound response      → ``{"jsonrpc":"2.0","id":N,"result":..}`` / ``{..,"error":..}``
- inbound notification  → ``{"jsonrpc":"2.0","method":..,"params":..}`` (no ``id``)
- inbound server→client request → has BOTH ``method`` and ``id`` (e.g.
  ``session/request_permission``); answered with :meth:`AcpConnection.respond`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

log = logging.getLogger("agent.acp")

# Callback signatures (may be sync or async; async results are scheduled).
NotificationCallback = Callable[[str, dict], Any]
ServerRequestCallback = Callable[[str, dict, int], Any]

# ``hermes acp --accept-hooks``: the long-lived Agent Client Protocol adapter.
# ``--accept-hooks`` auto-approves terminal tools (old ``--yolo``) so no
# ``session/request_permission`` round-trips are expected (ADR-0022 PoC).
HERMES_ACP_COMMAND = ("hermes", "acp", "--accept-hooks")

# JSON-RPC ``initialize`` protocol version (ACP v1, Hermes 0.18.2 — ADR-0022).
PROTOCOL_VERSION = 1

# Cold ``initialize`` measured ~8.5s in the PoC; the default must comfortably
# exceed that so a slow-but-healthy start is not mistaken for a dead process.
INIT_TIMEOUT_S = 20.0

# Bounded stderr capture: log at most this many bytes of the child's stderr so a
# spewing process cannot flood the log; enough for a crash post-mortem.
STDERR_LOG_LIMIT = 4096

# On shutdown, wait this long for a terminated process to exit before killing it.
ACLOSE_WAIT_S = 5.0


class AcpError(Exception):
    """A JSON-RPC error response, or a transport failure on a pending request.

    ``error`` carries the raw JSON-RPC ``error`` object (``{"code", "message",
    ...}``) when the failure came from an error response; it is ``None`` for
    transport-level failures (connection closed / EOF).
    """

    def __init__(self, message: str, *, error: Optional[dict] = None) -> None:
        super().__init__(message)
        self.error = error


class AcpCancelled(AcpError):
    """A prompt was cancelled locally (via :meth:`AcpClient.cancel_prompt`).

    Distinct from a transport :class:`AcpError` so a caller can tell an
    operator-requested cancellation apart from an honest crash/timeout failure.
    """


@dataclass(frozen=True)
class AcpToolEvent:
    """One tool-boundary event parsed from a ``session/update`` notification.

    Emitted for ``update.sessionUpdate`` of ``tool_call`` (the START edge) and
    ``tool_call_update`` (the FINISH edge) — the two edges the manager narrates
    on (ADR-0022). ``title`` carries the humanisable tool label (e.g.
    ``"terminal: uname -a"``), ``kind`` the ACP tool category (``execute`` …),
    ``status`` its lifecycle state (``pending`` / ``completed`` …). ``raw`` is the
    full ``session/update`` params for callers that need more than these fields.
    """

    kind_of_update: str
    tool_call_id: Optional[str]
    title: Optional[str]
    kind: Optional[str]
    status: Optional[str]
    raw: dict


# Queue sentinel: pushed onto a prompt's event queue to terminate its stream.
_STREAM_END = object()


@dataclass
class _PromptSession:
    """Per-session routing state for one in-flight ``session/prompt``.

    ``queue`` carries :class:`AcpToolEvent`s (terminated by ``_STREAM_END``);
    ``text_parts`` accumulates ``agent_message_chunk`` text as the streaming
    fallback for the final answer (see :meth:`AcpClient._extract_answer`). ``task``
    is the fire-and-forget RPC task so :meth:`AcpClient.cancel_prompt` can cancel
    it locally.
    """

    queue: "asyncio.Queue[Any]" = field(default_factory=asyncio.Queue)
    text_parts: list[str] = field(default_factory=list)
    task: Optional[asyncio.Task] = None
    _closed: bool = False

    def close(self) -> None:
        """Terminate the event stream exactly once (idempotent)."""
        if not self._closed:
            self._closed = True
            self.queue.put_nowait(_STREAM_END)


class AcpPromptHandle:
    """Handle over one in-flight ``session/prompt``: a tool-event stream + result.

    Two independently awaitable surfaces that compose with ``asyncio.wait_for``:

    - :attr:`events` — an async iterator of :class:`AcpToolEvent`s that terminates
      when the prompt completes (or crashes/cancels). Consume it to narrate.
    - :attr:`result` — an :class:`asyncio.Future` resolved with the final answer
      text on success, or failed with :class:`AcpError` / :class:`AcpCancelled` on
      crash / cancellation. This is the fast-window racer's target.

    Both are driven off the same per-session routing state, so tool events for one
    session never leak into another's stream (ADR-0022 concurrent-session
    interleaving).
    """

    def __init__(
        self, session_id: str, session: _PromptSession, result: "asyncio.Future[str]"
    ) -> None:
        self.session_id = session_id
        self._session = session
        self.result = result
        self.events: AsyncIterator[AcpToolEvent] = self._event_stream()

    async def _event_stream(self) -> AsyncIterator[AcpToolEvent]:
        queue = self._session.queue
        while True:
            item = await queue.get()
            if item is _STREAM_END:
                return
            yield item


class AcpConnection:
    """A JSON-RPC 2.0 codec over an ndjson ``asyncio`` stream pair.

    Owns a monotonic request-id counter and a background reader loop that
    dispatches every inbound line. Pending requests are tracked as futures so
    :meth:`request` awaits its own response; :meth:`aclose` and reader-loop
    teardown fail all pending futures so callers never hang.

    The reader loop is started on construction and therefore requires a running
    event loop. Malformed lines are logged and skipped — the loop never dies on
    bad input, only on EOF, cancellation, or an unexpected error.
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        on_notification: Optional[NotificationCallback] = None,
        on_server_request: Optional[ServerRequestCallback] = None,
    ) -> None:
        self._reader = reader
        self._writer = writer
        # Settable after construction as well (e.g. a supervisor wiring a handler
        # that needs a reference to this connection to call `respond`).
        self.on_notification = on_notification
        self.on_server_request = on_server_request
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._closed = False
        # Tracked separately from `_closed`: the reader loop sets `_closed` when
        # it exits on its own (EOF/error), but only `aclose()` releases the
        # writer — so EOF-then-aclose must still close the writer exactly once.
        self._writer_closed = False
        self._reader_task = asyncio.ensure_future(self._reader_loop())

    # -- Outbound ----------------------------------------------------------

    async def request(self, method: str, params: dict) -> dict:
        """Send a request and await its result.

        Returns the JSON-RPC ``result``. Raises :class:`AcpError` on an error
        response or if the connection is (or becomes) closed while waiting.
        """
        if self._closed:
            raise AcpError("ACP connection is closed")
        self._next_id += 1
        req_id = self._next_id
        fut: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        try:
            await self._write(
                {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            )
        except Exception:
            # Write failed: drop the registration so the future can't leak.
            self._pending.pop(req_id, None)
            raise
        return await fut

    def notify(self, method: str, params: Optional[dict] = None) -> None:
        """Fire-and-forget an outbound notification (no id, no response).

        Synchronous best-effort write; the ndjson framing is queued on the
        writer without awaiting drain, matching a notification's fire-and-forget
        semantics.
        """
        line = (
            json.dumps(
                {"jsonrpc": "2.0", "method": method, "params": params or {}}
            )
            + "\n"
        )
        self._writer.write(line.encode("utf-8"))

    async def respond(self, request_id: int, result: dict) -> None:
        """Answer a server→client request with a JSON-RPC result response."""
        await self._write({"jsonrpc": "2.0", "id": request_id, "result": result})

    async def _write(self, obj: dict) -> None:
        data = (json.dumps(obj) + "\n").encode("utf-8")
        self._writer.write(data)
        await self._writer.drain()

    # -- Inbound -----------------------------------------------------------

    async def _reader_loop(self) -> None:
        """Read and dispatch inbound lines until EOF, cancel, or error.

        On any exit (EOF, unexpected error, or cancellation) all pending futures
        are failed so callers never hang.
        """
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break  # EOF — peer closed the stream.
                try:
                    msg = json.loads(line)
                except (ValueError, TypeError):
                    log.warning("ACP: dropping unparseable line: %r", line[:200])
                    continue
                self._dispatch(msg)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the loop must never crash silently
            log.exception("ACP: reader loop terminated unexpectedly")
        finally:
            self._closed = True
            self._fail_pending(AcpError("ACP connection closed"))

    def _dispatch(self, msg: Any) -> None:
        if not isinstance(msg, dict):
            log.warning("ACP: dropping non-object message: %r", msg)
            return
        method = msg.get("method")
        msg_id = msg.get("id")
        if method is not None and msg_id is not None:
            # Server → client request (e.g. session/request_permission).
            self._invoke(
                self.on_server_request, method, msg.get("params") or {}, msg_id
            )
            return
        if method is not None:
            # Notification (no id).
            self._invoke(self.on_notification, method, msg.get("params") or {})
            return
        if msg_id is not None:
            # Response to one of our requests.
            self._resolve(msg_id, msg)
            return
        log.warning("ACP: dropping message with neither id nor method: %r", msg)

    def _resolve(self, msg_id: Any, msg: dict) -> None:
        fut = self._pending.pop(msg_id, None)
        if fut is None:
            log.warning("ACP: response for unknown id %r", msg_id)
            return
        if fut.done():
            return
        if "error" in msg:
            err = msg["error"]
            message = (
                err.get("message")
                if isinstance(err, dict) and err.get("message")
                else str(err)
            )
            fut.set_exception(AcpError(message, error=err))
        else:
            fut.set_result(msg.get("result"))

    def _invoke(self, cb: Optional[Callable[..., Any]], *args: Any) -> None:
        """Call a dispatch callback (sync or async), never raising into the loop."""
        if cb is None:
            return
        try:
            result = cb(*args)
        except Exception:  # noqa: BLE001 — a bad callback must not kill the loop
            log.exception("ACP: dispatch callback raised")
            return
        if asyncio.iscoroutine(result):
            asyncio.ensure_future(self._await_callback(result))

    async def _await_callback(self, coro: Awaitable[Any]) -> None:
        try:
            await coro
        except Exception:  # noqa: BLE001 — async callbacks are best-effort too
            log.exception("ACP: async dispatch callback raised")

    # -- Teardown ----------------------------------------------------------

    def _fail_pending(self, exc: AcpError) -> None:
        pending, self._pending = self._pending, {}
        for fut in pending.values():
            if not fut.done():
                fut.set_exception(exc)

    async def aclose(self) -> None:
        """Cancel the reader loop, fail pending requests, and close the writer.

        Idempotent, and safe to call after the reader loop already exited on its
        own (peer EOF / loop error): the writer is still released in that case —
        `_closed` alone must not short-circuit the writer close.
        """
        self._closed = True
        if not self._reader_task.done():
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        # The reader loop's `finally` fails pending futures; do it again here
        # (idempotent — `_pending` is already drained) to cover the case where
        # the loop had already exited before cancellation landed.
        self._fail_pending(AcpError("ACP connection closed"))
        if not self._writer_closed:
            self._writer_closed = True
            with contextlib.suppress(Exception):
                self._writer.close()


class AcpClient:
    """Supervisor for the long-lived ``hermes acp`` process (ADR-0022).

    Owns one persistent ``hermes acp --accept-hooks`` subprocess and the
    :class:`AcpConnection` codec over its stdio. Responsibilities:

    - **Spawn + initialize** (:meth:`start`) at worker startup. Cold ``initialize``
      is ~8.5s, so it happens eagerly rather than on first delegation.
    - **Graceful degradation.** Nothing here is startup-fatal: a missing binary, a
      slow/absent ``initialize``, or a crash leaves the client in a *dead* state
      and the voice agent keeps running Hermes-less (see ``worker_tools.py`` module
      docstring). :meth:`start` never raises; :attr:`available` lets callers answer
      "Hermes недоступен" honestly.
    - **Crash detection + honest failure.** A monitor task awaits ``proc.wait()``;
      on exit it marks the client dead and closes the connection, which fails every
      in-flight request future with :class:`AcpError` (the "fail in-flight
      honestly" mechanism). The child's stderr is drained (bounded) for diagnostics.
    - **Respawn on demand.** Respawn is lazy — :meth:`_ensure` spawns + initializes
      a fresh process when the current one is dead. There is no background retry
      loop, so repeated failures cannot tight-loop: each caller pays exactly one
      spawn+initialize attempt and gets an honest :class:`AcpError` if it fails.

    Per-session methods (:meth:`new_session` / :meth:`prompt` / :meth:`cancel_prompt`)
    obtain a live codec from :meth:`_ensure`, the single seam that transparently
    spans respawns. The client interposes its own dispatchers on every new
    connection: :meth:`prompt` routes ``session/update`` events per ``sessionId``
    into a streaming :class:`AcpPromptHandle`, and ``session/request_permission`` is
    auto-answered positively; user-supplied ``on_notification`` /
    ``on_server_request`` hooks are delegated to (the latter only for non-permission
    methods) so narration keeps routing across a respawn.
    """

    def __init__(
        self,
        command: tuple[str, ...] = HERMES_ACP_COMMAND,
        *,
        init_timeout_s: float = INIT_TIMEOUT_S,
        cwd: Optional[str] = None,
        on_notification: Optional[NotificationCallback] = None,
        on_server_request: Optional[ServerRequestCallback] = None,
    ) -> None:
        self._command = tuple(command)
        self._init_timeout_s = init_timeout_s
        self._cwd = cwd
        # User-supplied hooks, re-applied to each spawned connection so dispatch
        # keeps working across respawns. The client interposes its OWN dispatchers
        # (session routing + permission auto-answer) and delegates to these: every
        # notification is forwarded; a server→client request goes to the user hook
        # only for NON-permission methods (permission is always auto-answered).
        self._user_on_notification = on_notification
        self._user_on_server_request = on_server_request
        # Per-session routing for streaming prompts, keyed by ACP sessionId.
        self._prompt_sessions: dict[str, _PromptSession] = {}

        # Lifecycle state, all mutated under `_lock` (mirrors DesktopTTS._ensure_ws
        # / _drop_ws in tts_plugin.py: one lock serialises spawn/drop/respawn).
        self._lock = asyncio.Lock()
        self._proc: Optional["asyncio.subprocess.Process"] = None
        self._conn: Optional[AcpConnection] = None
        self._initialized = False
        self._monitor: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._closing = False

    # -- Public surface ----------------------------------------------------

    @property
    def available(self) -> bool:
        """True iff the process is alive AND the ``initialize`` handshake completed.

        Callers gate honest degradation on this ("Hermes недоступен" when False).
        The ``returncode is None`` check flips to False the instant the process
        dies, even before the monitor task has run its bookkeeping.
        """
        return (
            self._initialized
            and self._conn is not None
            and self._proc is not None
            and self._proc.returncode is None
        )

    async def start(self) -> bool:
        """Spawn ``hermes acp`` and run the ``initialize`` handshake.

        Returns True on success, False on ANY failure — a missing binary
        (``FileNotFoundError``), an ``initialize`` timeout, or an immediate crash.
        Never raises: the voice agent must start and keep running without Hermes
        (graceful-degradation doctrine). A False result leaves the client dead;
        the next :meth:`_ensure` will retry the spawn on demand.
        """
        async with self._lock:
            if self._closing:
                return False
            try:
                await self._spawn_locked()
            except Exception as exc:  # noqa: BLE001 — start is never fatal
                self._log_spawn_failure(exc)
                await self._drop_locked()
                return False
            return True

    async def aclose(self) -> None:
        """Shut the supervisor down: terminate ``hermes acp``, close the connection.

        Bounded and best-effort (never raises): cancel the monitor/stderr tasks,
        close the connection (failing any in-flight requests), then ``terminate``
        the process and wait briefly before ``kill``. Idempotent — after this the
        client stays dead (``_closing`` blocks further respawns).
        """
        async with self._lock:
            self._closing = True
            proc = self._proc
            conn = self._conn
            monitor, self._monitor = self._monitor, None
            stderr_task, self._stderr_task = self._stderr_task, None
            self._proc = None
            self._conn = None
            self._initialized = False
            if monitor is not None:
                monitor.cancel()
            if stderr_task is not None:
                stderr_task.cancel()
            if conn is not None:
                with contextlib.suppress(Exception):
                    await conn.aclose()
            if proc is not None and proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=ACLOSE_WAIT_S)
                except Exception:  # noqa: BLE001 — timeout or wait error → hard kill
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()

    # -- Sessions & streaming prompts --------------------------------------

    async def new_session(
        self, cwd: Optional[str] = None, mcp_servers: Optional[list] = None
    ) -> str:
        """Open a fresh ACP session (one per delegated task) and return its id.

        Sends ``session/new {cwd, mcpServers}`` over a live (respawned-on-demand)
        connection; ``cwd`` defaults to the client's configured cwd or the process
        cwd. Raises :class:`AcpError` if Hermes cannot be brought up.
        """
        conn = await self._ensure()
        effective_cwd = cwd if cwd is not None else (self._cwd or str(Path.cwd()))
        result = await conn.request(
            "session/new", {"cwd": effective_cwd, "mcpServers": mcp_servers or []}
        )
        return result["sessionId"]

    async def prompt(self, session_id: str, text: str) -> AcpPromptHandle:
        """Start a streaming ``session/prompt`` and return its :class:`AcpPromptHandle`.

        Registers the session's routing state BEFORE firing the RPC (so no
        ``session/update`` can race ahead of the queue), then fires
        ``session/prompt {sessionId, prompt:[{type:text,text}]}`` as a background
        task. Tool events stream on ``handle.events``; the final answer resolves
        ``handle.result`` (or fails it on crash/cancel). Routing is per-``sessionId``
        so concurrent prompts never cross-contaminate.

        One prompt per session at a time: a second :meth:`prompt` on a session whose
        prior prompt is still in flight raises :class:`AcpError`. Updates are routed
        by ``sessionId`` alone, so two in-flight prompts on one session would
        cross-contaminate the new handle's stream with the old RPC's late updates.
        The task manager opens one session per task (ADR-0022), so this never fires
        in normal use — start a new session (or :meth:`cancel_prompt` first) instead.
        """
        if session_id in self._prompt_sessions:
            raise AcpError(f"prompt already in flight for session {session_id}")
        # Register BEFORE any await: the in-flight check above must be atomic with
        # the registration (no yield between them, or two racing prompt() calls on
        # one session could both pass), and an early update must be routed, not
        # dropped, once the RPC fires.
        session = _PromptSession()
        self._prompt_sessions[session_id] = session
        try:
            conn = await self._ensure()
        except BaseException:
            # Hermes could not be brought up: undo the registration so the session
            # is not permanently blocked, and settle the (unreturned) stream.
            session.close()
            if self._prompt_sessions.get(session_id) is session:
                self._prompt_sessions.pop(session_id, None)
            raise
        result: "asyncio.Future[str]" = asyncio.get_event_loop().create_future()
        session.task = asyncio.ensure_future(
            self._run_prompt(conn, session_id, text, session, result)
        )
        return AcpPromptHandle(session_id, session, result)

    async def cancel_prompt(self, session_id: str) -> None:
        """Cancel an in-flight prompt: best-effort ``session/cancel`` + local abort.

        ADR-0022 does not mandate a cancel RPC, so this is defensive: it fires a
        best-effort ``session/cancel`` notification (the standard ACP method) and
        then cancels the local RPC task, which fails ``handle.result`` with
        :class:`AcpCancelled` and terminates the event stream. A no-op if the
        session is unknown.
        """
        session = self._prompt_sessions.get(session_id)
        conn = self._conn
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.notify("session/cancel", {"sessionId": session_id})
        if session is not None and session.task is not None:
            session.task.cancel()

    async def _run_prompt(
        self,
        conn: AcpConnection,
        session_id: str,
        text: str,
        session: _PromptSession,
        result: "asyncio.Future[str]",
    ) -> None:
        """Drive one ``session/prompt`` RPC and settle its handle.

        On success resolves ``result`` with the final answer; on crash (the pending
        future fails with :class:`AcpError`) or local cancellation fails it. Always
        terminates the event stream and drops the session's routing state so no
        consumer hangs and no state leaks.
        """
        try:
            rpc_result = await conn.request(
                "session/prompt",
                {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]},
            )
            if not result.done():
                result.set_result(self._extract_answer(rpc_result, session))
        except asyncio.CancelledError:
            if not result.done():
                result.set_exception(AcpCancelled("prompt cancelled"))
            raise
        except AcpError as exc:
            if not result.done():
                result.set_exception(exc)
        except Exception as exc:  # noqa: BLE001 — surface anything as an honest error
            if not result.done():
                result.set_exception(AcpError(f"prompt failed: {exc}"))
        finally:
            session.close()
            # Only drop our own entry (a racing re-prompt may have replaced it).
            if self._prompt_sessions.get(session_id) is session:
                self._prompt_sessions.pop(session_id, None)

    def _extract_answer(self, rpc_result: Any, session: _PromptSession) -> str:
        """Resolve the final answer text for a completed prompt.

        Prefer explicit text in the ``session/prompt`` result if present; otherwise
        fall back to the ``agent_message_chunk`` text accumulated during the stream.
        ADR-0022 OPEN POINT: the exact result shape for Hermes 0.18.2 is unconfirmed
        (the PoC only observed a ``stopReason``), so the accumulated-chunk fallback
        is always maintained and used when the result carries no obvious text.
        """
        text = self._explicit_result_text(rpc_result)
        if text is not None:
            return text
        return "".join(session.text_parts)

    @staticmethod
    def _explicit_result_text(rpc_result: Any) -> Optional[str]:
        """Best-effort extraction of a text answer from the RPC result object."""
        if not isinstance(rpc_result, dict):
            return None
        for key in ("text", "answer", "response", "content", "message"):
            val = rpc_result.get(key)
            if isinstance(val, str) and val:
                return val
            if isinstance(val, dict):
                inner = val.get("text")
                if isinstance(inner, str) and inner:
                    return inner
        return None

    # -- Dispatch (session routing + permission auto-answer) ---------------

    def _dispatch_notification(self, method: str, params: dict) -> Any:
        """Route ``session/update`` to per-session state, then delegate to the hook."""
        if method == "session/update" and isinstance(params, dict):
            self._route_session_update(params)
        if self._user_on_notification is not None:
            return self._user_on_notification(method, params)
        return None

    def _route_session_update(self, params: dict) -> None:
        """Fan a ``session/update`` to its session: tool events queued, text kept."""
        session = self._prompt_sessions.get(params.get("sessionId"))
        if session is None:
            return  # unknown / already-finished session — drop
        update = params.get("update")
        if not isinstance(update, dict):
            return
        kind_of_update = update.get("sessionUpdate")
        if kind_of_update in ("tool_call", "tool_call_update"):
            session.queue.put_nowait(
                AcpToolEvent(
                    kind_of_update=kind_of_update,
                    tool_call_id=update.get("toolCallId"),
                    title=update.get("title"),
                    kind=update.get("kind"),
                    status=update.get("status"),
                    raw=params,
                )
            )
        elif kind_of_update == "agent_message_chunk":
            content = update.get("content")
            if isinstance(content, dict):
                chunk = content.get("text")
                if isinstance(chunk, str) and chunk:
                    session.text_parts.append(chunk)
        # Other update kinds are ignored (the final answer arrives via the RPC).

    def _dispatch_server_request(
        self, conn: AcpConnection, method: str, params: dict, request_id: int
    ) -> Any:
        """Auto-answer ``session/request_permission``; else defer to the user hook."""
        if method == "session/request_permission":
            return self._auto_answer_permission(conn, params or {}, request_id)
        if self._user_on_server_request is not None:
            return self._user_on_server_request(method, params, request_id)
        log.warning(
            "ACP: no handler for server→client request %r (id=%s)", method, request_id
        )
        return None

    async def _auto_answer_permission(
        self, conn: AcpConnection, params: dict, request_id: int
    ) -> None:
        """Positively answer a permission request; never raise into the reader loop.

        Picks the first ``allow``-kind option (else the first option with an id) and
        replies ``{"outcome": {"outcome": "selected", "optionId": …}}``; if no
        options parse, replies with a generic ``selected`` outcome so the prompt is
        not blocked. With ``--accept-hooks`` these should be rare (ADR-0022 PoC).
        """
        try:
            option_id = self._pick_allow_option(params)
            if option_id is not None:
                outcome = {"outcome": "selected", "optionId": option_id}
            else:
                outcome = {"outcome": "selected"}
            await conn.respond(request_id, {"outcome": outcome})
            log.info(
                "ACP: auto-approved session/request_permission "
                "(session=%s, optionId=%s)",
                params.get("sessionId"),
                option_id,
            )
        except Exception:  # noqa: BLE001 — a permission reply must never crash the loop
            log.exception("ACP: failed to auto-answer permission request")

    @staticmethod
    def _pick_allow_option(params: dict) -> Optional[str]:
        """Choose an ``optionId`` to grant: first ``allow*`` kind, else first with id."""
        options = params.get("options")
        if not isinstance(options, list) or not options:
            return None
        for opt in options:
            if isinstance(opt, dict) and str(opt.get("kind", "")).startswith("allow"):
                if opt.get("optionId") is not None:
                    return opt["optionId"]
        for opt in options:
            if isinstance(opt, dict) and opt.get("optionId") is not None:
                return opt["optionId"]
        return None

    # -- Lifecycle seam ----------------------------------------------------

    async def _ensure(self) -> AcpConnection:
        """Return a live, initialized :class:`AcpConnection`, respawning on demand.

        The single seam per-session methods build on: if the process is up it
        returns the current codec; if it is dead (never started, crashed, or
        dropped) it tears down any stale state and spawns + initializes a fresh
        process (the respawn path). Raises :class:`AcpError` if Hermes cannot be
        brought up, so callers answer honestly instead of hanging.

        On-demand respawn (no background loop) is deliberate: repeated failures
        cost one spawn+initialize per call and can never tight-loop.
        """
        async with self._lock:
            if self._closing:
                raise AcpError("ACP client is shutting down")
            conn = self._conn
            if self.available and conn is not None:
                return conn
            # Dead → clear any stale process/connection, then spawn fresh.
            await self._drop_locked()
            try:
                return await self._spawn_locked()
            except Exception as exc:  # noqa: BLE001 — surface as an honest AcpError
                self._log_spawn_failure(exc)
                await self._drop_locked()
                raise AcpError(f"hermes acp unavailable: {exc}") from exc

    # -- Internals (caller holds `_lock`) ----------------------------------

    async def _spawn_locked(self) -> AcpConnection:
        """Spawn ``hermes acp`` and run ``initialize``. Raises on any failure.

        Wires a fresh :class:`AcpConnection` (with the stored hooks), a crash
        monitor, and a bounded stderr drain, then blocks on ``initialize`` under
        :attr:`_init_timeout_s`. On timeout/error the partially-started state is
        left for the caller to :meth:`_drop_locked`.
        """
        proc = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
        )
        self._proc = proc
        conn = AcpConnection(proc.stdout, proc.stdin)
        # Interpose the client's own dispatchers (they close over `conn` so the
        # permission auto-answer replies on the right connection across respawns).
        conn.on_notification = self._dispatch_notification
        conn.on_server_request = (
            lambda method, params, request_id: self._dispatch_server_request(
                conn, method, params, request_id
            )
        )
        self._conn = conn
        self._monitor = asyncio.ensure_future(self._monitor_proc(proc, conn))
        self._stderr_task = asyncio.ensure_future(self._drain_stderr(proc))
        await asyncio.wait_for(
            conn.request("initialize", {"protocolVersion": PROTOCOL_VERSION}),
            self._init_timeout_s,
        )
        self._initialized = True
        log.info("ACP: hermes acp initialized (pid=%s)", proc.pid)
        return conn

    async def _drop_locked(self) -> None:
        """Tear down the current process + connection (best-effort). Holds `_lock`.

        Cancels the monitor/stderr tasks, closes the connection (failing in-flight
        requests), and hard-kills the process. Idempotent and safe when nothing is
        running (e.g. after a ``FileNotFoundError`` spawn that set nothing).
        """
        self._initialized = False
        proc, self._proc = self._proc, None
        conn, self._conn = self._conn, None
        monitor, self._monitor = self._monitor, None
        stderr_task, self._stderr_task = self._stderr_task, None
        if monitor is not None:
            monitor.cancel()
        if stderr_task is not None:
            stderr_task.cancel()
        if conn is not None:
            with contextlib.suppress(Exception):
                await conn.aclose()
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()

    async def _monitor_proc(
        self, proc: "asyncio.subprocess.Process", conn: AcpConnection
    ) -> None:
        """Await the process exit, then mark dead + fail in-flight work honestly.

        Runs for the life of one process. On exit it clears state ONLY if this is
        still the current process (a racing :meth:`_ensure`/:meth:`aclose` under the
        lock may already have replaced it), then closes the connection — which
        fails every pending request future with :class:`AcpError`. Respawn is left
        to the next :meth:`_ensure`.
        """
        try:
            rc = await proc.wait()
        except asyncio.CancelledError:
            raise
        stderr_task = None
        async with self._lock:
            if self._proc is not proc:
                return  # already dropped/replaced under the lock
            log.warning(
                "ACP: hermes acp exited (rc=%s); Hermes unavailable, "
                "will respawn on next use",
                rc,
            )
            self._initialized = False
            self._proc = None
            self._conn = None
            self._monitor = None
            stderr_task, self._stderr_task = self._stderr_task, None
        if stderr_task is not None:
            stderr_task.cancel()
        # Fail all in-flight requests honestly (also idempotent with any aclose).
        with contextlib.suppress(Exception):
            await conn.aclose()

    async def _drain_stderr(self, proc: "asyncio.subprocess.Process") -> None:
        """Log the child's stderr (bounded to :data:`STDERR_LOG_LIMIT`) for diagnostics."""
        logged = 0
        try:
            while logged < STDERR_LOG_LIMIT:
                line = await proc.stderr.readline()
                if not line:
                    break  # EOF — the process closed stderr / exited.
                text = line.decode("utf-8", "replace").rstrip()
                if text:
                    log.warning("ACP hermes stderr: %s", text)
                logged += len(line)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — diagnostics must never crash the loop
            log.debug("ACP: stderr drain ended unexpectedly", exc_info=True)

    def _log_spawn_failure(self, exc: BaseException) -> None:
        """Log a spawn/initialize failure at the right granularity (never raises)."""
        if isinstance(exc, FileNotFoundError):
            log.warning(
                "ACP: `%s` not found — Hermes delegation disabled", self._command[0]
            )
        elif isinstance(exc, asyncio.TimeoutError):
            log.warning(
                "ACP: initialize timed out after %.1fs — Hermes unavailable",
                self._init_timeout_s,
            )
        else:
            log.warning(
                "ACP: failed to start `hermes acp` (%s: %s) — Hermes unavailable",
                type(exc).__name__,
                exc,
            )
