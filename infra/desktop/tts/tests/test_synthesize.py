"""Tests for the Qwen3-TTS synthesis adapter (load/stream/resample/speaker)."""

import numpy as np
import pytest

import synthesize


class _FakeModel:
    """Yields canned (audio, sr) chunks and records the call kwargs."""

    def __init__(self, chunks):
        self._chunks = chunks
        self.calls = []

    def generate_custom_voice_streaming(self, **kwargs):
        self.calls.append(kwargs)
        for audio, sr in self._chunks:
            yield audio, sr


@pytest.fixture(autouse=True)
def reset_model():
    prev = synthesize._model
    synthesize._model = None
    yield
    synthesize._model = prev


def test_is_loaded_reflects_model_presence():
    assert synthesize.is_loaded() is False
    synthesize._model = object()
    assert synthesize.is_loaded() is True


def test_load_model_uses_from_pretrained(monkeypatch):
    sentinel = object()
    captured = {}

    class _FQ:
        @classmethod
        def from_pretrained(cls, name):
            captured["name"] = name
            return sentinel

    import faster_qwen3_tts

    monkeypatch.setattr(faster_qwen3_tts, "FasterQwen3TTS", _FQ)
    out = synthesize.load_model()
    assert out is sentinel
    assert synthesize._model is sentinel
    assert synthesize.is_loaded() is True
    assert captured["name"] == synthesize.MODEL_NAME


def test_stream_pcm_yields_pcm16_bytes():
    audio = np.array([0.0, 1.0, -1.0], dtype=np.float32)
    synthesize._model = _FakeModel([(audio, 24000)])
    out = list(synthesize.stream_pcm("привет"))
    assert len(out) == 1
    ints = np.frombuffer(out[0], dtype="<i2")
    assert list(ints) == [0, 32767, -32767]


def test_stream_pcm_resamples_to_24k():
    audio = np.zeros(48000, dtype=np.float32)  # 1s @ 48k
    synthesize._model = _FakeModel([(audio, 48000)])
    out = b"".join(synthesize.stream_pcm("text"))
    n_samples = len(out) // 2
    assert abs(n_samples - 24000) < 100  # ~1s @ 24k


def test_stream_pcm_multiple_chunks():
    a = np.zeros(10, dtype=np.float32)
    b = np.zeros(20, dtype=np.float32)
    synthesize._model = _FakeModel([(a, 24000), (b, 24000)])
    out = list(synthesize.stream_pcm("text"))
    assert len(out) == 2
    assert len(out[0]) == 20  # 10 samples * 2 bytes
    assert len(out[1]) == 40


def test_stream_pcm_uses_configured_speaker_by_default():
    model = _FakeModel([(np.zeros(4, dtype=np.float32), 24000)])
    synthesize._model = model
    list(synthesize.stream_pcm("text", voice="default"))
    assert model.calls[-1]["speaker"] == synthesize.SPEAKER
    assert model.calls[-1]["language"] == synthesize.LANGUAGE
    assert model.calls[-1]["chunk_size"] == synthesize.CHUNK_SIZE


@pytest.mark.parametrize("voice", ["bob", "alice"])
def test_stream_pcm_overrides_speaker_with_voice(voice):
    model = _FakeModel([(np.zeros(4, dtype=np.float32), 24000)])
    synthesize._model = model
    list(synthesize.stream_pcm("text", voice=voice))
    assert model.calls[-1]["speaker"] == voice


@pytest.mark.parametrize("voice", [None, "", "default"])
def test_stream_pcm_falls_back_to_speaker_for_empty_voice(voice):
    model = _FakeModel([(np.zeros(4, dtype=np.float32), 24000)])
    synthesize._model = model
    list(synthesize.stream_pcm("text", voice=voice))
    assert model.calls[-1]["speaker"] == synthesize.SPEAKER
