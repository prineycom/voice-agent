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
    stt_ws_url: str
    stt_health_url: str
    stt_sample_rate: int
    stt_language: str
    tts_ws_url: str
    tts_health_url: str
    tts_voice: str
    tts_sample_rate: int
    tts_streaming: bool
    agent_greeting: str
    worker_port: int
    # LLM (via local LiteLLM proxy)
    llm_base_url: str  # the /v1 suffix is mandatory for the openai plugin
    llm_model: str  # LiteLLM alias defined in litellm config
    llm_api_key: str  # must equal the LiteLLM master key when one is set
    # Reasoning effort passed to the LLM. Default "none" keeps replies snappy for
    # voice (reasoning models otherwise spend latency "thinking" before the first
    # token). Set empty to omit the param for models/providers that reject it;
    # "low"/"minimal" are alternatives.
    llm_reasoning_effort: str
    soul_path: Path
    # Worker skill (Hermes CLI patterns) appended to instructions. Optional —
    # missing file is warned, not fatal (unlike SOUL).
    worker_skill_path: Path


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

    raw_stt_sample_rate = os.environ.get("STT_SAMPLE_RATE", "16000")
    try:
        stt_sample_rate = int(raw_stt_sample_rate)
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid STT_SAMPLE_RATE={raw_stt_sample_rate!r}: expected an integer."
        ) from exc

    raw_sample_rate = os.environ.get("TTS_SAMPLE_RATE", "24000")
    try:
        tts_sample_rate = int(raw_sample_rate)
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid TTS_SAMPLE_RATE={raw_sample_rate!r}: expected an integer."
        ) from exc

    tts_streaming = os.environ.get("TTS_STREAMING", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    raw_worker_port = os.environ.get("AGENT_WORKER_PORT", "8090")
    try:
        worker_port = int(raw_worker_port)
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid AGENT_WORKER_PORT={raw_worker_port!r}: expected an integer."
        ) from exc

    return AgentConfig(
        livekit_url=os.environ.get("LIVEKIT_URL", "ws://localhost:7880"),
        livekit_api_key=os.environ["LIVEKIT_API_KEY"],
        livekit_api_secret=os.environ["LIVEKIT_API_SECRET"],
        stt_ws_url=os.environ.get("STT_WS_URL", "ws://100.75.88.35:8001/stt"),
        stt_health_url=os.environ.get(
            "STT_HEALTH_URL", "http://100.75.88.35:8001/health"
        ),
        stt_sample_rate=stt_sample_rate,
        stt_language=os.environ.get("STT_LANGUAGE", "ru"),
        tts_ws_url=os.environ.get("TTS_WS_URL", "ws://100.75.88.35:8002/tts"),
        tts_health_url=os.environ.get(
            "TTS_HEALTH_URL", "http://100.75.88.35:8002/health"
        ),
        tts_voice=os.environ.get("TTS_VOICE", "default"),
        tts_sample_rate=tts_sample_rate,
        tts_streaming=tts_streaming,
        agent_greeting=os.environ.get("AGENT_GREETING", DEFAULT_GREETING),
        worker_port=worker_port,
        llm_base_url=os.environ.get("LLM_BASE_URL", "http://localhost:4000/v1"),
        llm_model=os.environ.get("LLM_MODEL", "voice-agent"),
        llm_api_key=os.environ.get("LLM_API_KEY", "litellm-local"),
        llm_reasoning_effort=os.environ.get("LLM_REASONING_EFFORT", "none"),
        soul_path=Path(
            os.environ.get(
                "SOUL_PATH",
                str(Path(__file__).resolve().parent / "SOUL.md"),
            )
        ),
        worker_skill_path=Path(
            os.environ.get(
                "WORKER_SKILL_PATH",
                str(Path(__file__).resolve().parent / "skills" / "hermes.md"),
            )
        ),
    )
