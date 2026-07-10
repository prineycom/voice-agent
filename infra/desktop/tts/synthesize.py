"""TTS synthesis facade (engine-dispatched).

The actual synthesis lives in `engines.py` (pluggable strategies). This module
keeps the historical `synthesize.load_model` / `synthesize.stream_pcm` /
`synthesize.is_loaded` surface the server and tests already import, and forwards
to the engine selected by `TTS_ENGINE` in `.env`.

Kept for backward compat:
- `MODEL_NAME`, `SPEAKER`, `LANGUAGE`, `CHUNK_SIZE` — exposed for the `/health`
  report and tests. They mirror the CustomVoice engine's config when that engine
  is selected; otherwise they reflect the selected engine's model_name/language.
- `stream_pcm(text, voice)` — delegates to the active engine.
"""

from __future__ import annotations

import logging
import os

import engines as _engines_mod
from engines import Engines, TTSEngine

log = logging.getLogger("tts.synthesize")

_engine: TTSEngine | None = None


def _ensure_engine() -> TTSEngine:
    global _engine
    if _engine is None:
        _engine = Engines.from_env()
    return _engine


def load_model():
    """Load the selected engine's model into VRAM. Call once at startup."""
    eng = _ensure_engine()
    eng.load()
    return eng


def is_loaded() -> bool:
    return _engine is not None and getattr(_engine, "_model", None) is not None


def unload_model():
    """Release the engine's model from VRAM. Idempotent.

    Sets the engine's `_model` to None so the GPU memory is freed when GC runs.
    The engine instance itself is kept so `engine()` and health fields still work;
    call `load_model()` to restore the model.
    """
    if _engine is not None:
        _engine._model = None
    return _engine


def stream_pcm(text: str, voice: str = "default", emotion=None):
    """Yield 24kHz mono int16 PCM byte chunks for `text`. Delegates to the engine.

    `emotion` (enum str / A2E vector) is forwarded; engines that can't express it
    ignore it (only VoxCPM uses it, as a style prefix).
    """
    eng = _ensure_engine()
    yield from eng.stream_pcm(text, voice, emotion)


def engine() -> TTSEngine:
    """The active engine instance (for server health fields)."""
    return _ensure_engine()


# --- backward-compat module-level attributes ------------------------------- #
# These exist so old code that reads `synthesize.MODEL_NAME` etc. keeps working.
# They reflect the active engine's config and are populated lazily.

def __getattr__(name: str):
    # Called only when the attribute isn't found the normal way.
    eng = _ensure_engine()
    if name == "MODEL_NAME":
        return eng.model_name
    if name == "LANGUAGE":
        return eng.language
    if name == "CHUNK_SIZE":
        return getattr(eng, "chunk_size", None)
    if name == "SPEAKER":
        # Only CustomVoice has a `speaker`; others don't. Mirror old behaviour.
        return getattr(eng, "speaker", None)
    raise AttributeError(name)