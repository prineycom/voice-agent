"""Behavioral tests for the TTS health gate against a real fake /health server."""

import pytest

from health import TTSHealthError, check_tts_health


@pytest.mark.asyncio
async def test_ok_returns_dict(health_server):
    body = await check_tts_health(health_server.ok_url)
    assert body["model_loaded"] is True
    assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_degraded_raises(health_server):
    with pytest.raises(TTSHealthError):
        await check_tts_health(health_server.degraded_url)


@pytest.mark.asyncio
async def test_connection_refused_raises():
    # Port 1 on localhost is effectively never an open TTS service.
    with pytest.raises(TTSHealthError):
        await check_tts_health("http://127.0.0.1:1/health", timeout=2.0)
