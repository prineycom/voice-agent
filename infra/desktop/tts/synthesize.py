"""Qwen3-TTS synthesis adapter.

Isolates the model call so the WebSocket server stays backend-agnostic.
`stream_pcm` yields 24kHz mono int16 PCM byte chunks.

Backend method name is confirmed via introspection (plan Task D5). Default path
uses CustomVoice streaming; see FALLBACKS at the bottom if that method is absent.
"""

import logging
import os

import numpy as np

from audio import float32_to_pcm16, resample_to_24k

log = logging.getLogger("tts.synthesize")

MODEL_NAME = os.getenv("TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")
SPEAKER = os.getenv("TTS_SPEAKER", "aiden")
LANGUAGE = os.getenv("TTS_LANGUAGE", "Russian")
CHUNK_SIZE = int(os.getenv("TTS_CHUNK_SIZE", "4"))

_model = None


def load_model():
    """Load the Qwen3-TTS model into VRAM. Call once at startup."""
    global _model
    from faster_qwen3_tts import FasterQwen3TTS

    _model = FasterQwen3TTS.from_pretrained(MODEL_NAME)
    return _model


def is_loaded() -> bool:
    return _model is not None


def stream_pcm(text: str, voice: str = "default"):
    """Yield 24kHz mono int16 PCM byte chunks for `text`.

    `voice` overrides the configured speaker unless it is empty/"default".
    """
    speaker = SPEAKER if voice in (None, "", "default") else voice
    for audio_chunk, sr, *_ in _model.generate_custom_voice_streaming(
        text=text,
        language=LANGUAGE,
        speaker=speaker,
        chunk_size=CHUNK_SIZE,
    ):
        samples = np.asarray(audio_chunk, dtype=np.float32).reshape(-1)
        samples = resample_to_24k(samples, int(sr))
        yield float32_to_pcm16(samples)


# FALLBACKS (apply in Task D5 if generate_custom_voice_streaming is not exposed):
#
# (a) Non-streaming custom voice — wrap the single returned array as one chunk:
#       audio, sr = _model.generate_custom_voice(text=text, language=LANGUAGE,
#                                                 speaker=speaker)
#       yield float32_to_pcm16(resample_to_24k(np.asarray(audio, np.float32), int(sr)))
#
# (b) Voice clone streaming (requires a Russian reference clip on disk):
#       for audio_chunk, sr, *_ in _model.generate_voice_clone_streaming(
#           text=text, language=LANGUAGE, ref_audio=REF_WAV, ref_text=REF_TEXT,
#           chunk_size=CHUNK_SIZE):
#           ...
#
# (c) Official package `qwen-tts`:
#       from qwen_tts import Qwen3TTSModel
#       model.generate_custom_voice(...) with its documented signature.
