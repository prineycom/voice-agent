"""Agent Worker config — loads `.env` and exposes a validated AgentConfig.

Required keys (LIVEKIT_API_KEY / LIVEKIT_API_SECRET) fail fast if missing.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_GREETING = "Привет! Я голосовой ассистент. Чем могу помочь?"


@dataclass(frozen=True)
class AgentConfig:
    livekit_url: str
    livekit_api_key: str
    livekit_api_secret: str
    tts_ws_url: str
    tts_health_url: str
    tts_voice: str
    tts_sample_rate: int
    agent_greeting: str


def load_config() -> AgentConfig:
    """Load `.env` from the package dir and build a validated AgentConfig."""
    load_dotenv(Path(__file__).resolve().parent / ".env")

    missing = [
        key
        for key in ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
        if not os.environ.get(key)
    ]
    if missing:
        raise RuntimeError(
            f"Missing required env var(s): {', '.join(missing)}. "
            "Copy them from infra/pi/.env into the agent .env "
            "(infra/pi/agent/.env)."
        )

    raw_sample_rate = os.environ.get("TTS_SAMPLE_RATE", "24000")
    try:
        tts_sample_rate = int(raw_sample_rate)
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid TTS_SAMPLE_RATE={raw_sample_rate!r}: expected an integer."
        ) from exc

    return AgentConfig(
        livekit_url=os.environ.get("LIVEKIT_URL", "ws://localhost:7880"),
        livekit_api_key=os.environ["LIVEKIT_API_KEY"],
        livekit_api_secret=os.environ["LIVEKIT_API_SECRET"],
        tts_ws_url=os.environ.get("TTS_WS_URL", "ws://100.75.88.35:8002/tts"),
        tts_health_url=os.environ.get(
            "TTS_HEALTH_URL", "http://100.75.88.35:8002/health"
        ),
        tts_voice=os.environ.get("TTS_VOICE", "default"),
        tts_sample_rate=tts_sample_rate,
        agent_greeting=os.environ.get("AGENT_GREETING", DEFAULT_GREETING),
    )
