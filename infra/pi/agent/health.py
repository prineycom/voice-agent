"""Startup gate for the Desktop STT/TTS services.

Before the Agent Worker accepts a room, it must confirm the GPU boxes are
reachable and have their models loaded. We GET the Desktop `/health` endpoints
and abort loudly otherwise — a silent failure here means a deaf or mute agent.

Gate key: `model_loaded` (bool). Each server (infra/desktop/tts/server.py and
infra/desktop/stt/server.py) returns HTTP 200 + `{"status": "ok", ...,
"model_loaded": true}` when the model is ready, and HTTP 503 + `{"status":
"degraded", "model_loaded": false}` while it is still loading or failed to
load. We require *both* HTTP 200 and a truthy `model_loaded`, so a degraded box
never slips through.
"""

import asyncio
import logging

import aiohttp

log = logging.getLogger("agent.health")


class TTSHealthError(RuntimeError):
    """Raised when the Desktop TTS service is unreachable or not ready."""


class STTHealthError(RuntimeError):
    """Raised when the Desktop STT service is unreachable or not ready."""


async def _check_health(
    health_url: str,
    service_name: str,
    error_cls: type[Exception],
    timeout: float = 5.0,
    retries: int = 4,
    retry_delay: float = 0.75,
) -> dict:
    """GET a Desktop `/health` endpoint and apply the readiness gate, with retries.

    Returns the parsed health dict on success (HTTP 200 + `model_loaded` true).
    Retries up to ``retries`` times (``retry_delay`` seconds apart) before giving
    up: the Desktop path is reached over a Tailscale direct link that can stall
    for a few seconds when it has been idle, and a single transient timeout must
    not abort the whole session. Raises ``error_cls`` only after every attempt
    fails (non-200, falsy `model_loaded`, connection refused, timeout, or any
    other aiohttp client error).
    """
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return await _check_once(health_url, service_name, error_cls, timeout)
        except error_cls as exc:
            last_exc = exc
            if attempt < retries:
                log.warning(
                    "%s health attempt %d/%d failed (%s); retrying in %.2fs",
                    service_name, attempt, retries, exc, retry_delay,
                )
                await asyncio.sleep(retry_delay)
    assert last_exc is not None
    raise last_exc


async def _check_once(
    health_url: str,
    service_name: str,
    error_cls: type[Exception],
    timeout: float,
) -> dict:
    """Single GET against a Desktop `/health` endpoint + readiness gate."""
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    try:
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.get(health_url) as resp:
                if resp.status != 200:
                    raise error_cls(_msg(service_name, health_url, f"HTTP {resp.status}"))
                body = await resp.json()
    except error_cls:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        log.error("%s health check failed for %s — %s", service_name, health_url, reason)
        raise error_cls(_msg(service_name, health_url, reason)) from exc

    if not body.get("model_loaded"):
        reason = f"model_loaded={body.get('model_loaded')!r}, status={body.get('status')!r}"
        log.error("%s health check failed for %s — %s", service_name, health_url, reason)
        raise error_cls(_msg(service_name, health_url, reason))

    return body


async def check_tts_health(health_url: str, timeout: float = 5.0) -> dict:
    """GET the Desktop TTS `/health` endpoint; raise if it is not ready.

    Returns the parsed health dict on success (HTTP 200 + `model_loaded` true).
    Raises :class:`TTSHealthError` with an actionable message for any failure:
    non-200, falsy `model_loaded`, connection refused, timeout, or any other
    aiohttp client error.
    """
    return await _check_health(
        health_url, service_name="TTS", error_cls=TTSHealthError, timeout=timeout
    )


async def check_stt_health(health_url: str, timeout: float = 5.0) -> dict:
    """GET the Desktop STT `/health` endpoint; raise if it is not ready.

    Returns the parsed health dict on success (HTTP 200 + `model_loaded` true).
    Raises :class:`STTHealthError` with an actionable message for any failure:
    non-200, falsy `model_loaded`, connection refused, timeout, or any other
    aiohttp client error.
    """
    return await _check_health(
        health_url, service_name="STT", error_cls=STTHealthError, timeout=timeout
    )


def _msg(service_name: str, url: str, reason: str) -> str:
    return (
        f"Desktop {service_name} unavailable at {url} — is the service running "
        f"and is Tailscale up? ({reason})"
    )
