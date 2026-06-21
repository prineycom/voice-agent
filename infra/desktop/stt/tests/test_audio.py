import numpy as np
from audio import pcm16_to_float32


def test_pcm16_to_float32_scales_to_unit_range():
    data = np.array([-32768, 0, 32767], dtype="<i2").tobytes()
    out = pcm16_to_float32(data)
    assert out.dtype == np.float32
    np.testing.assert_allclose(out, [-1.0, 0.0, 32767 / 32768], rtol=1e-6)


def test_pcm16_to_float32_drops_odd_trailing_byte():
    data = b"\x00\x00\x01"  # 3 bytes -> 1 valid sample
    out = pcm16_to_float32(data)
    assert len(out) == 1


def test_pcm16_to_float32_empty():
    out = pcm16_to_float32(b"")
    assert len(out) == 0
