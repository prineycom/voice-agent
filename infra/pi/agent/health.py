"""Startup gate for the Desktop TTS service.

Before the Agent Worker accepts a room, it must confirm the GPU TTS box is
reachable and has its model loaded. We GET the Desktop `/health` endpoint and
abort loudly otherwise — a silent failure here means a mute agent.

Gate key: `model_loaded` (bool). The server (infra/desktop/tts/server.py)
returns HTTP 200 + `{"status": "ok", ..., "model_loaded": true}` when the model
is ready, and HTTP 503 + `{"status": "degraded", "model_loaded": false}` while
it is still loading or failed to load. We require *both* HTTP 200 and a truthy
`model_loaded`, so a degraded box never slips through.
"""

import asyncio
import logging

import aiohttp

log = logging.getLogger("agent.health")


class TTSHealthError(RuntimeError):
    """Raised when the Desktop TTS service is unreachable or not ready."""


async def check_tts_health(health_url: str, timeout: float = 5.0) -> dict:
    """GET the Desktop TTS `/health` endpoint; raise if it is not ready.

    Returns the parsed health dict on success (HTTP 200 + `model_loaded` true).
    Raises :class:`TTSHealthError` with an actionable message for any failure:
    non-200, falsy `model_loaded`, connection refused, timeout, or any other
    aiohttp client error.
    """
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    try:
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.get(health_url) as resp:
                if resp.status != 200:
                    raise TTSHealthError(_msg(health_url, f"HTTP {resp.status}"))
                body = await resp.json()
    except TTSHealthError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        log.error("TTS health check failed for %s — %s", health_url, reason)
        raise TTSHealthError(_msg(health_url, reason)) from exc

    if not body.get("model_loaded"):
        reason = f"model_loaded={body.get('model_loaded')!r}, status={body.get('status')!r}"
        log.error("TTS health check failed for %s — %s", health_url, reason)
        raise TTSHealthError(_msg(health_url, reason))

    return body


def _msg(url: str, reason: str) -> str:
    return (
        f"Desktop TTS unavailable at {url} — is the service running and is "
        f"Tailscale up? ({reason})"
    )
