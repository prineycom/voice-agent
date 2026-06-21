import numpy as np
from audio import float32_to_pcm16, resample_to_24k


def test_float32_to_pcm16_scales_and_clips():
    out = float32_to_pcm16(np.array([0.0, 1.0, -1.0, 2.0, -2.0], dtype=np.float32))
    ints = np.frombuffer(out, dtype="<i2")
    assert list(ints) == [0, 32767, -32767, 32767, -32767]


def test_resample_to_24k_noop_when_already_24k():
    x = np.zeros(100, dtype=np.float32)
    out = resample_to_24k(x, 24000)
    assert len(out) == 100
    assert out.dtype == np.float32


def test_resample_to_24k_halves_length_from_48k():
    x = np.zeros(48000, dtype=np.float32)  # 1s @ 48k
    out = resample_to_24k(x, 48000)
    assert abs(len(out) - 24000) < 100  # ~1s @ 24k
