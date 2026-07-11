"""Minimal web test-harness server for the Voice Agent.

Serves the single-file test page and mints short-lived LiveKit join tokens.
stdlib-only (http.server) plus livekit.api for token signing — no FastAPI, no
build step. Credentials are read from the Agent Worker's .env so there is one
source of truth for the LiveKit API key/secret.

Run:
    LIVEKIT_WS_URL=wss://rpi.darter-smoot.ts.net:8443 \
        python3 server.py        # binds 127.0.0.1:8080

Routes:
    GET /                      -> index.html
    GET /token?identity=<name> -> {"token": "...", "url": "wss://..."}
    GET /healthz              -> "ok"
    GET /static/...           -> static asset

The public WSS URL handed to the browser is LIVEKIT_WS_URL (the tailscale-serve
HTTPS endpoint in front of LiveKit), NOT the internal ws://localhost:7880 the
worker uses.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from livekit.api import AccessToken, DeleteRoomRequest, LiveKitAPI, VideoGrants

HERE = Path(__file__).resolve().parent


def _load_voice_state():
    """Load the agent worker's pure-stdlib `voice_state` module by path.

    The agent worker owns the active-Voice state (catalog + persisted global);
    this front door just exposes it over HTTP same-origin for the frontend voice
    switcher (ADR-0020 / #48). Loaded via importlib rather than
    `sys.path.insert(agent_dir)` so the sibling agent package's modules
    (config.py, health.py, agent.py, …) can never shadow same-named top-level
    imports elsewhere in this process.
    """
    path = HERE.parent / "agent" / "voice_state.py"
    spec = importlib.util.spec_from_file_location("voice_state", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


voice_state = _load_voice_state()
INDEX_HTML = HERE / "index.html"
FACE3D_HTML = HERE / "face3d.html"
STATIC_ROOT = HERE / "static"
# The Agent Worker's .env is the single source of truth for LiveKit creds.
AGENT_ENV = HERE.parent / "agent" / ".env"

# Content types for static assets, keyed by file suffix.
_STATIC_CONTENT_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
}

ROOM = os.environ.get("LIVEKIT_ROOM", "test")
BIND_HOST = os.environ.get("WEB_BIND_HOST", "127.0.0.1")
BIND_PORT = int(os.environ.get("WEB_BIND_PORT", "8080"))

# Reset the shared room on every /token request (i.e. on each fresh connect).
# The harness reuses one fixed room, so a hung agent/participant session can
# wedge it; deleting the room first forces LiveKit to recreate it clean and
# re-dispatch the agent when the new participant joins. Default on; set
# WEB_RESET_ROOM_ON_TOKEN=0 to disable.
RESET_ROOM_ON_TOKEN = os.environ.get("WEB_RESET_ROOM_ON_TOKEN", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
    "",
)
# Upper bound (seconds) on the room-reset API call, so a down LiveKit never
# stalls token issuance / the join.
RESET_ROOM_TIMEOUT = float(os.environ.get("WEB_RESET_ROOM_TIMEOUT", "3"))


def _load_env_file(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE .env file (ignores comments/blank lines).

    Values are taken verbatim after the first '=', so spaces and punctuation in a
    value (e.g. the Russian greeting) are preserved without shell quoting rules.
    """
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


_ENV = _load_env_file(AGENT_ENV)


def _cred(name: str) -> str:
    """Resolve a credential from the process env first, then the agent .env."""
    value = os.environ.get(name) or _ENV.get(name)
    if not value:
        raise RuntimeError(
            f"{name} not set (checked process env and {AGENT_ENV}). "
            "The token server cannot sign join tokens without it."
        )
    return value


def _public_ws_url() -> str:
    """The WSS endpoint the browser connects to (tailscale-serve in front of LiveKit)."""
    return os.environ.get("LIVEKIT_WS_URL", "wss://rpi.darter-smoot.ts.net:8443")


def _server_api_url() -> str:
    """Internal LiveKit server URL for the management API (RoomService).

    This is the on-Pi LiveKit address the worker uses (LIVEKIT_URL, default
    ws://localhost:7880) — NOT the public tailscale wss the browser connects to.
    LiveKitAPI speaks HTTP, so ws(s):// is rewritten to http(s)://.
    """
    url = os.environ.get("LIVEKIT_URL") or _ENV.get("LIVEKIT_URL") or "http://localhost:7880"
    if url.startswith("ws://"):
        url = "http://" + url[len("ws://") :]
    elif url.startswith("wss://"):
        url = "https://" + url[len("wss://") :]
    return url


async def _delete_room_async() -> None:
    lkapi = LiveKitAPI(
        _server_api_url(), _cred("LIVEKIT_API_KEY"), _cred("LIVEKIT_API_SECRET")
    )
    try:
        await lkapi.room.delete_room(DeleteRoomRequest(room=ROOM))
    finally:
        await lkapi.aclose()


def _reset_room() -> None:
    """Delete the shared room so the next join starts from a clean state.

    Best-effort and non-fatal: if the room doesn't exist or the LiveKit API is
    briefly unreachable, the join still proceeds (LiveKit auto-creates the room
    on join), so a reset failure must never block token issuance.
    """
    try:
        # Bound the whole call so a down/unreachable LiveKit can never stall the
        # join behind the aiohttp default timeout.
        asyncio.run(asyncio.wait_for(_delete_room_async(), timeout=RESET_ROOM_TIMEOUT))
        print(f"[web] reset room '{ROOM}' before issuing token", flush=True)
    except Exception as exc:  # noqa: BLE001 — reset is best-effort, never fatal
        # not_found = the room had no active session (already clean) — the normal
        # case on a first/idle connect, not an error worth flagging.
        if getattr(exc, "code", None) == "not_found":
            print(f"[web] room '{ROOM}' already clean (no active session)", flush=True)
        else:
            print(f"[web] room reset skipped ({exc.__class__.__name__}: {exc})", flush=True)


def _mint_token(identity: str) -> str:
    grants = VideoGrants(room_join=True, room=ROOM)
    token = (
        AccessToken(_cred("LIVEKIT_API_KEY"), _cred("LIVEKIT_API_SECRET"))
        .with_identity(identity)
        .with_name(identity)
        .with_grants(grants)
        .with_ttl(timedelta(hours=12))
    )
    return token.to_jwt()


class Handler(BaseHTTPRequestHandler):
    server_version = "VoiceAgentTestHarness/1.0"

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Single-purpose private tool; allow the page's same-origin fetch.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802 (stdlib naming)
        route = urlparse(self.path).path
        if route != "/voice":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        # Set the active Voice (persisted global). Body: {"voice": "<preset>"}.
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw or b"{}")
            name = body.get("voice") if isinstance(body, dict) else None
        except ValueError:
            self._send_json(400, {"error": "invalid JSON body"})
            return
        try:
            voice_state.set_active_voice(name)
        except (ValueError, TypeError) as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except OSError as exc:
            # Disk full / permission on the state dir: report a clean 500 rather
            # than letting the handler crash with a traceback + broken response.
            self._send_json(500, {"error": f"could not persist voice: {exc}"})
            return
        # Echo the new state so the caller (and the UI) reflects it immediately.
        self._send_json(200, voice_state.state())

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        parsed = urlparse(self.path)
        route = parsed.path

        if route == "/" or route == "/index.html":
            if not INDEX_HTML.exists():
                self._send(500, b"index.html missing", "text/plain; charset=utf-8")
                return
            self._send(
                200,
                INDEX_HTML.read_bytes(),
                "text/html; charset=utf-8",
            )
            return

        if route == "/face3d" or route == "/face3d.html":
            if not FACE3D_HTML.exists():
                self._send(500, b"face3d.html missing", "text/plain; charset=utf-8")
                return
            self._send(
                200,
                FACE3D_HTML.read_bytes(),
                "text/html; charset=utf-8",
            )
            return

        if route == "/healthz":
            self._send(200, b"ok", "text/plain; charset=utf-8")
            return

        if route == "/voices":
            # The switchable Voice catalog + which is active (persisted global).
            self._send_json(200, voice_state.state())
            return

        if route == "/token":
            params = parse_qs(parsed.query)
            identity = (params.get("identity", ["guest"])[0] or "guest").strip()[:64]
            # Fresh connect -> clear any wedged session so this join starts clean.
            if RESET_ROOM_ON_TOKEN:
                _reset_room()
            try:
                payload = {
                    "token": _mint_token(identity),
                    "url": _public_ws_url(),
                    "room": ROOM,
                    "identity": identity,
                }
            except RuntimeError as exc:
                self._send(
                    500,
                    json.dumps({"error": str(exc)}).encode(),
                    "application/json; charset=utf-8",
                )
                return
            self._send(
                200,
                json.dumps(payload).encode(),
                "application/json; charset=utf-8",
            )
            return

        if route.startswith("/static/"):
            rel = route[len("/static/") :]
            target = (STATIC_ROOT / rel).resolve()
            # Containment check: reject paths that escape STATIC_ROOT (traversal).
            if not target.is_relative_to(STATIC_ROOT.resolve()) or not target.is_file():
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            content_type = _STATIC_CONTENT_TYPES.get(
                target.suffix, "application/octet-stream"
            )
            self._send(200, target.read_bytes(), content_type)
            return

        self._send(404, b"not found", "text/plain; charset=utf-8")

    def log_message(self, fmt: str, *args) -> None:  # quieter, single-line logs
        print(f"[web] {self.address_string()} {fmt % args}", flush=True)


def main() -> None:
    httpd = ThreadingHTTPServer((BIND_HOST, BIND_PORT), Handler)
    print(
        f"[web] serving test harness on http://{BIND_HOST}:{BIND_PORT} "
        f"(room={ROOM}, ws={_public_ws_url()})",
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
