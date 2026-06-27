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

import json
import os
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from livekit.api import AccessToken, VideoGrants

HERE = Path(__file__).resolve().parent
INDEX_HTML = HERE / "index.html"
STATIC_ROOT = HERE / "static"
# The Agent Worker's .env is the single source of truth for LiveKit creds.
AGENT_ENV = HERE.parent / "agent" / ".env"

# Content types for static assets, keyed by file suffix.
_STATIC_CONTENT_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}

ROOM = os.environ.get("LIVEKIT_ROOM", "test")
BIND_HOST = os.environ.get("WEB_BIND_HOST", "127.0.0.1")
BIND_PORT = int(os.environ.get("WEB_BIND_PORT", "8080"))


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

        if route == "/healthz":
            self._send(200, b"ok", "text/plain; charset=utf-8")
            return

        if route == "/token":
            params = parse_qs(parsed.query)
            identity = (params.get("identity", ["guest"])[0] or "guest").strip()[:64]
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
