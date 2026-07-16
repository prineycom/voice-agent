"""ACP (Agent Client Protocol) ndjson JSON-RPC transport for the long-lived
``hermes acp`` process, per docs/adr/0022-hermes-acp-hybrid-delegation.md.

This module provides :class:`AcpConnection`, a request/response + notification
codec over newline-delimited JSON (ndjson) JSON-RPC 2.0. It is deliberately
decoupled from any subprocess: it takes an ``asyncio`` stream reader/writer pair,
so a later subprocess supervisor (``AcpClient``) can spawn ``hermes acp`` and
compose this codec over its stdio without the codec knowing about processes.

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
        """Cancel the reader loop, fail pending requests, and close the writer."""
        if self._closed and self._reader_task.done():
            return
        self._closed = True
        self._reader_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._reader_task
        # The reader loop's `finally` fails pending futures; do it again here
        # (idempotent — `_pending` is already drained) to cover the case where
        # the loop had already exited before cancellation landed.
        self._fail_pending(AcpError("ACP connection closed"))
        with contextlib.suppress(Exception):
            self._writer.close()
