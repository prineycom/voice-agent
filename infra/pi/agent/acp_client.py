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
from typing import Any, Awaitable, Callable, Optional

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

    Per-session methods (``session/new`` / ``session/prompt``) are added on this
    class in a later task; they obtain a live codec from :meth:`_ensure`, which is
    the single seam that transparently spans respawns. The ``on_notification`` /
    ``on_server_request`` hooks are stored and handed to *every* new connection so
    ``session/update`` narration and permission requests keep routing across a
    respawn.
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
        # Stored once and re-applied to each spawned connection so dispatch keeps
        # working across respawns; the next task wires session routing through them.
        self._on_notification = on_notification
        self._on_server_request = on_server_request

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
        conn = AcpConnection(
            proc.stdout,
            proc.stdin,
            on_notification=self._on_notification,
            on_server_request=self._on_server_request,
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
