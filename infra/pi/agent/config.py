"""Agent Worker config — loads `.env` and exposes a validated AgentConfig.

Required keys (LIVEKIT_API_KEY / LIVEKIT_API_SECRET) fail fast if missing.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_GREETING = "Привет! Я голосовой ассистент. Чем могу помочь?"

_TRUTHY = {"1", "true", "yes", "on"}

# Wake-word artifacts live under infra/desktop/wakeword/ (committed with #57);
# the Pi checks them out with the repo. Resolve relative to this file so the
# defaults work regardless of cwd.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_WAKEWORD_DIR = _REPO_ROOT / "infra" / "desktop" / "wakeword"
_DEFAULT_WAKEWORD_MODELS = str(_WAKEWORD_DIR / "models" / "hey_jarvis.onnx")
_DEFAULT_WAKEWORD_THRESHOLDS = str(_WAKEWORD_DIR / "models" / "thresholds.json")


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean env var with a case-insensitive truthy-set.

    Treats "1"/"true"/"yes"/"on" (any case, surrounding whitespace ignored) as
    True and everything else as False. When the var is unset, returns `default`.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


def _load_thresholds(path: str) -> dict[str, float]:
    """Load the per-model wake-word threshold map (best-effort).

    Missing/unreadable file or non-numeric entries → empty map; the detector then
    falls back to WAKEWORD_THRESHOLD for every model. Keys prefixed with ``_``
    (``_comment``, ``_default``) are metadata and skipped.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, float] = {}
    for name, value in data.items():
        if name.startswith("_"):
            continue
        if isinstance(value, (int, float)):
            out[name] = float(value)
    return out


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
    # Wake-word activation (ADR-0021 / Epic #56). Master flag OFF = today's
    # always-listening behavior (no gate, greeting kept).
    wakeword_enabled: bool
    wakeword_model_paths: tuple[str, ...]
    wakeword_thresholds: dict[str, float]
    wakeword_threshold: float  # fallback for models absent from thresholds.json
    wakeword_stride_s: float
    # Active window: auto-sleep after this much silence (the follow-up strict-mode
    # flag is added in #59).
    wakeword_silence_timeout: float


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

    tts_streaming = _env_bool("TTS_STREAMING", default=True)

    raw_worker_port = os.environ.get("AGENT_WORKER_PORT", "8090")
    try:
        worker_port = int(raw_worker_port)
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid AGENT_WORKER_PORT={raw_worker_port!r}: expected an integer."
        ) from exc

    def _env_float(name: str, default: float) -> float:
        raw = os.environ.get(name)
        if raw is None or not raw.strip():
            return default
        try:
            return float(raw)
        except ValueError as exc:
            raise RuntimeError(f"Invalid {name}={raw!r}: expected a number.") from exc

    # Wake-word activation (ADR-0021). Model paths are comma-separated so the gate
    # runs one combined multi-keyword model or several single-keyword ones.
    raw_models = os.environ.get("WAKEWORD_MODEL_PATHS", _DEFAULT_WAKEWORD_MODELS)
    wakeword_model_paths = tuple(p.strip() for p in raw_models.split(",") if p.strip())
    wakeword_thresholds = _load_thresholds(
        os.environ.get("WAKEWORD_THRESHOLDS_PATH", _DEFAULT_WAKEWORD_THRESHOLDS)
    )

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
        wakeword_enabled=_env_bool("WAKEWORD_ENABLED", default=True),
        wakeword_model_paths=wakeword_model_paths,
        wakeword_thresholds=wakeword_thresholds,
        wakeword_threshold=_env_float("WAKEWORD_THRESHOLD", 0.5),
        wakeword_stride_s=_env_float("WAKEWORD_STRIDE_S", 0.5),
        wakeword_silence_timeout=_env_float("WAKEWORD_SILENCE_TIMEOUT", 8.0),
    )
