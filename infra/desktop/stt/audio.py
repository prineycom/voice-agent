"""Pure audio helpers for the STT service (no GPU, no model deps)."""

import numpy as np


def pcm16_to_float32(data: bytes) -> np.ndarray:
    """Convert little-endian 16-bit signed PCM bytes to float32 in [-1, 1).

    A trailing odd byte (incomplete sample) is dropped.
    """
    if len(data) % 2 != 0:
        data = data[: len(data) - 1]
    ints = np.frombuffer(data, dtype="<i2").astype(np.float32)
    return ints / 32768.0
