"""Behavioral tests for the TTS and STT health gates against a real fake /health server."""

import pytest

from health import STTHealthError, TTSHealthError, check_stt_health, check_tts_health


# ---------------------------------------------------------------------------
# TTS health gate
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# STT health gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stt_ok_returns_dict(health_server):
    body = await check_stt_health(health_server.ok_url)
    assert body["model_loaded"] is True
    assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_stt_degraded_raises(health_server):
    with pytest.raises(STTHealthError):
        await check_stt_health(health_server.degraded_url)


@pytest.mark.asyncio
async def test_stt_connection_refused_raises():
    # Port 1 on localhost is effectively never an open STT service.
    with pytest.raises(STTHealthError) as exc_info:
        await check_stt_health("http://127.0.0.1:1/health", timeout=2.0)
    assert "STT" in str(exc_info.value)
