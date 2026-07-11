"""TTS engines — pluggable voice-synthesis strategies for Qwen3-TTS models.

One abstract `TTSEngine` + a registry of concrete engines. The server picks the
engine from `TTS_ENGINE` in `.env` and delegates every request to it. Adding a new
model / voice-input mode = a new engine class + one line in `Engines.REGISTRY`;
`server.py` and the worker→TTS protocol (`{"text","voice"}`) never change.

Engines shipped today:
- **CustomVoiceEngine** — predefined speaker IDs (`aiden`, `ryan`, …).
  Model: `Qwen3-TTS-12Hz-1.7B-CustomVoice`. `voice` = speaker name; `default` falls
  back to `TTS_SPEAKER`.
- **VoiceCloneEngine** — voice cloning by reference audio + transcript.
  Model: `Qwen3-TTS-12Hz-1.7B-Base`. `voice` selects a named ref profile from the
  `TTS_VOICE_REFS` config map (e.g. `pasha=<wav>,<text>`); `default` uses the first
  one. A single `TTS_REF_AUDIO`/`TTS_REF_TEXT` pair is the simple one-voice case.
- **VoiceDesignEngine** — instruction-described voice ("warm confident narrator…").
  Model: `Qwen3-TTS-12Hz-1.7B-VoiceDesign`. `voice` = instruction string; `default`
  falls back to `TTS_INSTRUCT`.

All engines yield 24kHz mono int16 PCM byte chunks via `stream_pcm(text, voice)`,
so `server.py` is engine-agnostic.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from audio import float32_to_pcm16, resample_to_24k

log = logging.getLogger("tts.engines")

# 24kHz mono int16 PCM, the contract the server streams to the worker.
SAMPLE_RATE = 24000


def _emit_pcm(audio_chunk, sr: int) -> bytes:
    """Shared post-processing: float audio → resample to 24k → PCM16 bytes."""
    samples = np.asarray(audio_chunk, dtype=np.float32).reshape(-1)
    samples = resample_to_24k(samples, int(sr))
    return float32_to_pcm16(samples)


class TTSEngine(ABC):
    """Abstract TTS engine. One instance per server process; loaded once at startup."""

    #: Short id used in `TTS_ENGINE` and the `/health` report.
    name: str
    #: Model id this engine loads (read from .env in subclasses, exposed in health).
    model_name: str
    #: Language passed to the model.
    language: str

    @abstractmethod
    def load(self) -> None:
        """Load the model into VRAM. Called once at server startup."""

    @abstractmethod
    def stream_pcm(self, text: str, voice: str = "default", emotion=None) -> bytes:
        """Yield 24kHz mono int16 PCM byte chunks for `text`.

        `voice` is engine-specific (speaker name / ref profile / instruction);
        `default` is the engine's configured fallback. `emotion` is the per-sentence
        emotion tag (enum str, or A2E vector list); engines that cannot express it
        ignore it — only VoxCPM consumes it (as a style prefix).
        """
        raise NotImplementedError

    def health_fields(self) -> dict:
        """Extra fields this engine contributes to the `/health` response."""
        return {}


# --------------------------------------------------------------------------- #
# CustomVoice — predefined speaker IDs
# --------------------------------------------------------------------------- #
class CustomVoiceEngine(TTSEngine):
    """Predefined speaker IDs (aiden, ryan, serena, …). Model: CustomVoice."""

    name = "custom_voice"

    def __init__(self) -> None:
        self.model_name = os.getenv("TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")
        self.language = os.getenv("TTS_LANGUAGE", "Russian")
        self.speaker = os.getenv("TTS_SPEAKER", "aiden")
        self.chunk_size = int(os.getenv("TTS_CHUNK_SIZE", "4"))
        self._model = None

    def load(self) -> None:
        from faster_qwen3_tts import FasterQwen3TTS

        self._model = FasterQwen3TTS.from_pretrained(self.model_name)

    def stream_pcm(self, text: str, voice: str = "default", emotion=None):
        speaker = self.speaker if voice in (None, "", "default") else voice
        for audio_chunk, sr, *_ in self._model.generate_custom_voice_streaming(
            text=text,
            language=self.language,
            speaker=speaker,
            chunk_size=self.chunk_size,
        ):
            yield _emit_pcm(audio_chunk, sr)

    def health_fields(self) -> dict:
        return {"speaker": self.speaker}


# --------------------------------------------------------------------------- #
# VoiceClone — reference audio + transcript (voice cloning)
# --------------------------------------------------------------------------- #
@dataclass
class VoiceRef:
    """One reference voice profile: a WAV clip + its verbatim transcript."""

    name: str
    audio_path: Path
    ref_text: str


def _parse_refs() -> list[VoiceRef]:
    """Parse voice-clone reference profiles from .env.

    Simple case (one voice): `TTS_REF_AUDIO=<wav>` + `TTS_REF_TEXT=<text>`,
    named `default`.
    Multi-voice case: `TTS_VOICE_REFS=pasha,mama` + per-profile
    `TTS_REF_pasha_AUDIO=<wav>`, `TTS_REF_pasha_TEXT=<text>`, …
    Both forms may be combined; named profiles take precedence. Returns at least
    one profile (the simple one) when only `TTS_REF_AUDIO`/`TTS_REF_TEXT` are set.
    """
    refs: list[VoiceRef] = []

    names = os.getenv("TTS_VOICE_REFS", "").strip()
    if names:
        for nm in [n.strip() for n in names.split(",") if n.strip()]:
            # Profile vars use the name verbatim (TTS_REF_<name>_AUDIO), matching
            # .env.example. Note: on Windows env lookups are case-insensitive, on
            # Linux they are not — so the docs and this lookup must agree exactly.
            wav = os.getenv(f"TTS_REF_{nm}_AUDIO")
            txt = os.getenv(f"TTS_REF_{nm}_TEXT")
            if not wav or not txt:
                raise RuntimeError(
                    f"voice_clone ref profile '{nm}' is incomplete: set both "
                    f"TTS_REF_{nm}_AUDIO and TTS_REF_{nm}_TEXT."
                )
            refs.append(VoiceRef(name=nm, audio_path=Path(wav), ref_text=txt))
    else:
        wav = os.getenv("TTS_REF_AUDIO")
        txt = os.getenv("TTS_REF_TEXT")
        if not wav or not txt:
            raise RuntimeError(
                "voice_clone engine requires reference audio + transcript. Set "
                "TTS_REF_AUDIO=<wav path> and TTS_REF_TEXT=<verbatim transcript>, "
                "or define named profiles via TTS_VOICE_REFS + TTS_REF_<NAME>_AUDIO/"
                "TTS_REF_<NAME>_TEXT."
            )
        refs.append(VoiceRef(name="default", audio_path=Path(wav), ref_text=txt))

    # Validate paths exist now (fail loud at startup, not on the first request).
    for r in refs:
        if not r.audio_path.is_file():
            raise RuntimeError(
                f"voice_clone ref '{r.name}': audio file not found at {r.audio_path}"
            )
    return refs


class VoiceCloneEngine(TTSEngine):
    """Voice cloning by reference audio + transcript. Model: Base."""

    name = "voice_clone"

    def __init__(self) -> None:
        self.model_name = os.getenv("TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-Base")
        self.language = os.getenv("TTS_LANGUAGE", "Russian")
        self.chunk_size = int(os.getenv("TTS_CHUNK_SIZE", "8"))
        # Sampling knobs (faster_qwen3_tts defaults: 0.9 / 1.0 / 50 / 1.05). We
        # default temperature/top_p a touch tighter for a steadier "stable voice"
        # (see the 2026-07-11 seed/emotion investigation). NO fixed seed on
        # purpose: a global seed freezes one random draw that is robotic / wrong
        # for some utterances — the sampling lottery, confirmed by listening.
        self.temperature = float(os.getenv("TTS_TEMPERATURE", "0.8"))
        self.top_p = float(os.getenv("TTS_TOP_P", "0.9"))
        self.top_k = int(os.getenv("TTS_TOP_K", "50"))
        self.repetition_penalty = float(os.getenv("TTS_REPETITION_PENALTY", "1.05"))
        # Optional single fixed style hint for the baseline voice (empty → none).
        # Per-utterance emotion via instruct is NOT reliable on the clone, so this
        # is one steady mood, not LLM-driven. Kept off by default.
        self.instruct = os.getenv("TTS_CLONE_INSTRUCT", "").strip() or None
        self.refs: dict[str, VoiceRef] = {r.name: r for r in _parse_refs()}
        # `default` resolves to the first configured profile.
        self._default_name = next(iter(self.refs))
        self._model = None

    def load(self) -> None:
        from faster_qwen3_tts import FasterQwen3TTS

        self._model = FasterQwen3TTS.from_pretrained(self.model_name)

    def _resolve(self, voice: str | None) -> VoiceRef:
        if voice in (None, "", "default"):
            return self.refs[self._default_name]
        if voice in self.refs:
            return self.refs[voice]
        raise RuntimeError(
            f"voice_clone: unknown ref profile {voice!r}. "
            f"Configured: {', '.join(self.refs)} (default={self._default_name!r})."
        )

    def stream_pcm(self, text: str, voice: str = "default", emotion=None):
        ref = self._resolve(voice)
        for audio_chunk, sr, *_ in self._model.generate_voice_clone_streaming(
            text=text,
            language=self.language,
            ref_audio=str(ref.audio_path),
            ref_text=ref.ref_text,
            chunk_size=self.chunk_size,
            temperature=self.temperature,
            top_p=self.top_p,
            top_k=self.top_k,
            repetition_penalty=self.repetition_penalty,
            instruct=self.instruct,
        ):
            yield _emit_pcm(audio_chunk, sr)

    def health_fields(self) -> dict:
        return {
            "ref_profiles": list(self.refs),
            "default_ref": self._default_name,
        }


# --------------------------------------------------------------------------- #
# VoiceDesign — instruction-described voice
# --------------------------------------------------------------------------- #
class VoiceDesignEngine(TTSEngine):
    """Instruction-described voice ("warm confident narrator…"). Model: VoiceDesign."""

    name = "voice_design"

    def __init__(self) -> None:
        self.model_name = os.getenv("TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign")
        self.language = os.getenv("TTS_LANGUAGE", "Russian")
        self.instruct = os.getenv("TTS_INSTRUCT", "")
        self.chunk_size = int(os.getenv("TTS_CHUNK_SIZE", "8"))
        self._model = None

    def load(self) -> None:
        from faster_qwen3_tts import FasterQwen3TTS

        self._model = FasterQwen3TTS.from_pretrained(self.model_name)

    def stream_pcm(self, text: str, voice: str = "default", emotion=None):
        instruct = self.instruct if voice in (None, "", "default") else voice
        if not instruct:
            raise RuntimeError(
                "voice_design engine requires an instruction. Set TTS_INSTRUCT or "
                "pass a non-default `voice` describing the desired voice."
            )
        for audio_chunk, sr, *_ in self._model.generate_voice_design_streaming(
            text=text,
            language=self.language,
            instruct=instruct,
            chunk_size=self.chunk_size,
        ):
            yield _emit_pcm(audio_chunk, sr)

    def health_fields(self) -> dict:
        return {"instruct": self.instruct}


# --------------------------------------------------------------------------- #
# VoxCPM — expressive voice cloning with per-sentence emotion (ADR-0018)
# --------------------------------------------------------------------------- #
# Emotion enum -> English style descriptor, injected as a leading "(...)" prefix.
# VoxCPM2 only interprets the prefix (vs speaking it) in *controllable* cloning
# mode (reference_wav_path, no transcript) — see the spike doc. `neutral`/unknown
# => no prefix (plain clone). Curated A2E-subset + intensity lands in a later pass
# (ADR-0018 step 4); this is the current 5-enum bridge.
VOXCPM_EMOTION_PROMPTS = {
    "neutral": "",
    "happy": "cheerful, upbeat, warm",
    "sad": "sad, subdued, slow",
    "surprised": "surprised, astonished",
    "thinking": "thoughtful, measured, calm",
}


class VoxCPMEngine(TTSEngine):
    """Expressive voice cloning (VoxCPM2). Emotion = leading English style prefix.

    Uses *controllable* cloning (`reference_wav_path`, no transcript) so the style
    prefix is interpreted, not spoken. Reuses the `TTS_VOICE_REFS`/`TTS_REF_AUDIO`
    profiles (only the audio path is used; `ref_text` is ignored here).
    """

    name = "voxcpm"

    def __init__(self) -> None:
        self.model_name = os.getenv("TTS_MODEL", "openbmb/VoxCPM2")
        self.language = os.getenv("TTS_LANGUAGE", "Auto")
        self.cfg_value = float(os.getenv("VOXCPM_CFG", "2.0"))
        self.timesteps = int(os.getenv("VOXCPM_TIMESTEPS", "10"))
        self.refs: dict[str, VoiceRef] = {r.name: r for r in _parse_refs()}
        self._default_name = next(iter(self.refs))
        self._sr = 48000  # VoxCPM2 native output; _emit_pcm resamples to 24k
        self._model = None

    def load(self) -> None:
        from voxcpm import VoxCPM

        self._model = VoxCPM.from_pretrained(self.model_name, load_denoiser=False)
        # Cold start is ~13s; warm the CUDA kernels once so the first real
        # utterance streams at the ~0.4s warm first-chunk latency (spike finding).
        try:
            ref = self.refs[self._default_name]
            for _ in self._model.generate_streaming(
                text="Прогрев.", reference_wav_path=str(ref.audio_path),
                cfg_value=self.cfg_value, inference_timesteps=self.timesteps,
            ):
                pass
            log.info("VoxCPM warmup complete")
        except Exception:  # noqa: BLE001 — warmup is best-effort
            log.warning("VoxCPM warmup failed", exc_info=True)

    def _resolve(self, voice: str | None) -> VoiceRef:
        if voice in (None, "", "default"):
            return self.refs[self._default_name]
        if voice in self.refs:
            return self.refs[voice]
        raise RuntimeError(
            f"voxcpm: unknown ref profile {voice!r}. Configured: {', '.join(self.refs)}."
        )

    @staticmethod
    def _style_prefix(emotion) -> str:
        """Build the leading '(...)' style prefix from the emotion tag.

        `neutral`/None/unknown/vector => "" (plain clone). String enum only for now.
        """
        if not isinstance(emotion, str):
            return ""
        desc = VOXCPM_EMOTION_PROMPTS.get(emotion.strip().lower(), "")
        return f"({desc})" if desc else ""

    def stream_pcm(self, text: str, voice: str = "default", emotion=None):
        ref = self._resolve(voice)
        styled = self._style_prefix(emotion) + text
        kw = dict(
            reference_wav_path=str(ref.audio_path),
            cfg_value=self.cfg_value,
            inference_timesteps=self.timesteps,
        )
        try:
            for chunk in self._model.generate_streaming(text=styled, **kw):
                yield _emit_pcm(chunk, self._sr)
        except TypeError:
            # streaming may reject a kwarg / mode on some builds → one-shot fallback
            yield _emit_pcm(self._model.generate(text=styled, **kw), self._sr)

    def health_fields(self) -> dict:
        return {
            "ref_profiles": list(self.refs),
            "default_ref": self._default_name,
            "timesteps": self.timesteps,
            "emotions": sorted(k for k, v in VOXCPM_EMOTION_PROMPTS.items() if v),
        }


# --------------------------------------------------------------------------- #
# Registry & selection
# --------------------------------------------------------------------------- #
class Engines:
    """Engine registry. Add a new engine in one place (REGISTRY)."""

    REGISTRY: dict[str, type[TTSEngine]] = {
        "custom_voice": CustomVoiceEngine,
        "voice_clone": VoiceCloneEngine,
        "voice_design": VoiceDesignEngine,
        "voxcpm": VoxCPMEngine,
    }

    @classmethod
    def from_env(cls) -> TTSEngine:
        """Construct the engine selected by `TTS_ENGINE` (default: custom_voice)."""
        key = os.getenv("TTS_ENGINE", "custom_voice").strip().lower()
        if key not in cls.REGISTRY:
            raise RuntimeError(
                f"Unknown TTS_ENGINE={key!r}. Available: {', '.join(cls.REGISTRY)}."
            )
        engine = cls.REGISTRY[key]()
        log.info("Selected TTS engine: %s (model=%s)", engine.name, engine.model_name)
        return engine


# Backward-compat shim: `synthesize.py` re-exports these so existing imports and
# tests keep working while the codebase migrates to engines.
__all__ = [
    "TTSEngine",
    "CustomVoiceEngine",
    "VoiceCloneEngine",
    "VoiceDesignEngine",
    "VoxCPMEngine",
    "Engines",
    "VoiceRef",
    "SAMPLE_RATE",
]