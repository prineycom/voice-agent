"""Pure audio helpers for the TTS service (no GPU, no model deps)."""

import numpy as np
import soxr


def float32_to_pcm16(samples: np.ndarray) -> bytes:
    """Convert float32 audio in [-1, 1] to little-endian 16-bit PCM bytes."""
    clipped = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    ints = (clipped * 32767.0).round().astype("<i2")
    return ints.tobytes()


def resample_to_24k(samples: np.ndarray, src_sr: int) -> np.ndarray:
    """Resample float32 mono audio to 24kHz. No-op when already 24kHz."""
    samples = np.asarray(samples, dtype=np.float32)
    if src_sr == 24000:
        return samples
    return soxr.resample(samples, src_sr, 24000).astype(np.float32)
