"""Tests for the TTS engine dispatch (engines.py) and the synthesize facade.

No GPU, no real model — each engine's `_model` is monkeypatched with a fake that
yields canned (audio, sr) chunks and records call kwargs. Tests cover:

- engine selection from `TTS_ENGINE` (custom_voice / voice_clone / voice_design);
- each engine's `stream_pcm` calls the right model method with the right kwargs;
- `voice` resolution rules per engine (default fallback, override, named profile);
- the `synthesize` facade forwards to the active engine and exposes backcompat attrs;
- voice_clone ref parsing (simple one-voice + multi-voice) and validation.
"""

import numpy as np
import pytest

import engines
import synthesize


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _FakeCustomVoiceModel:
    def __init__(self, chunks):
        self._chunks = chunks
        self.calls = []

    def generate_custom_voice_streaming(self, **kwargs):
        self.calls.append(kwargs)
        for audio, sr in self._chunks:
            yield audio, sr


class _FakeVoiceCloneModel:
    def __init__(self, chunks):
        self._chunks = chunks
        self.calls = []

    def generate_voice_clone_streaming(self, **kwargs):
        self.calls.append(kwargs)
        for audio, sr in self._chunks:
            yield audio, sr


class _FakeVoiceDesignModel:
    def __init__(self, chunks):
        self._chunks = chunks
        self.calls = []

    def generate_voice_design_streaming(self, **kwargs):
        self.calls.append(kwargs)
        for audio, sr in self._chunks:
            yield audio, sr


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def reset_engine():
    """Reset the cached engine + module state between tests."""
    synthesize._engine = None
    yield
    synthesize._engine = None


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch):
    """Default a clean env per test (tests set what they need explicitly)."""
    for k in [
        "TTS_ENGINE", "TTS_MODEL", "TTS_LANGUAGE", "TTS_SPEAKER", "TTS_CHUNK_SIZE",
        "TTS_INSTRUCT", "TTS_REF_AUDIO", "TTS_REF_TEXT", "TTS_VOICE_REFS",
    ]:
        monkeypatch.delenv(k, raising=False)


def _chunks():
    return [(np.zeros(10, dtype=np.float32), 24000)]


# --------------------------------------------------------------------------- #
# Engine selection
# --------------------------------------------------------------------------- #
def test_default_engine_is_custom_voice(monkeypatch):
    monkeypatch.delenv("TTS_ENGINE", raising=False)
    eng = engines.Engines.from_env()
    assert isinstance(eng, engines.CustomVoiceEngine)


def test_unknown_engine_raises(monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "nope")
    with pytest.raises(RuntimeError, match="Unknown TTS_ENGINE"):
        engines.Engines.from_env()


def test_select_voice_clone(monkeypatch, tmp_path):
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"fake")
    monkeypatch.setenv("TTS_ENGINE", "voice_clone")
    monkeypatch.setenv("TTS_REF_AUDIO", str(wav))
    monkeypatch.setenv("TTS_REF_TEXT", "привет")
    eng = engines.Engines.from_env()
    assert isinstance(eng, engines.VoiceCloneEngine)


def test_select_voice_design(monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "voice_design")
    eng = engines.Engines.from_env()
    assert isinstance(eng, engines.VoiceDesignEngine)


# --------------------------------------------------------------------------- #
# CustomVoiceEngine
# --------------------------------------------------------------------------- #
def test_custom_voice_stream_uses_configured_speaker(monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "custom_voice")
    monkeypatch.setenv("TTS_SPEAKER", "serena")
    eng = engines.Engines.from_env()
    m = _FakeCustomVoiceModel(_chunks())
    eng._model = m
    list(eng.stream_pcm("text"))
    assert m.calls[-1]["speaker"] == "serena"
    assert m.calls[-1]["language"] == "Russian"


@pytest.mark.parametrize("voice", ["bob", "alice"])
def test_custom_voice_overrides_speaker(monkeypatch, voice):
    monkeypatch.setenv("TTS_ENGINE", "custom_voice")
    monkeypatch.setenv("TTS_SPEAKER", "aiden")
    eng = engines.Engines.from_env()
    m = _FakeCustomVoiceModel(_chunks())
    eng._model = m
    list(eng.stream_pcm("text", voice=voice))
    assert m.calls[-1]["speaker"] == voice


@pytest.mark.parametrize("voice", [None, "", "default"])
def test_custom_voice_default_falls_back_to_speaker(monkeypatch, voice):
    monkeypatch.setenv("TTS_ENGINE", "custom_voice")
    monkeypatch.setenv("TTS_SPEAKER", "ryan")
    eng = engines.Engines.from_env()
    m = _FakeCustomVoiceModel(_chunks())
    eng._model = m
    list(eng.stream_pcm("text", voice=voice))
    assert m.calls[-1]["speaker"] == "ryan"


def test_custom_voice_yields_pcm16_bytes(monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "custom_voice")
    eng = engines.Engines.from_env()
    eng._model = _FakeCustomVoiceModel([(np.array([0.0, 1.0, -1.0], dtype=np.float32), 24000)])
    out = list(eng.stream_pcm("привет"))
    assert len(out) == 1
    assert list(np.frombuffer(out[0], dtype="<i2")) == [0, 32767, -32767]


def test_custom_voice_resamples_to_24k(monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "custom_voice")
    eng = engines.Engines.from_env()
    eng._model = _FakeCustomVoiceModel([(np.zeros(48000, dtype=np.float32), 48000)])
    out = b"".join(eng.stream_pcm("text"))
    assert abs(len(out) // 2 - 24000) < 100


# CustomVoice emotion → instruct (ADR-0020 / #51)
@pytest.mark.parametrize("emotion", ["happy", "SAD", "  Excited  ", "thinking", "angry"])
def test_custom_voice_known_emotion_sends_instruct(monkeypatch, emotion):
    monkeypatch.setenv("TTS_ENGINE", "custom_voice")
    eng = engines.Engines.from_env()
    m = _FakeCustomVoiceModel(_chunks())
    eng._model = m
    list(eng.stream_pcm("привет", emotion=emotion))
    instruct = m.calls[-1]["instruct"]
    assert instruct == engines.CUSTOMVOICE_EMOTION_INSTRUCT[emotion.strip().lower()]
    assert instruct  # non-empty clause


@pytest.mark.parametrize("emotion", [None, "neutral", "ecstatic", [0.0, 1.0, 0.0]])
def test_custom_voice_neutral_unknown_or_vector_sends_no_instruct(monkeypatch, emotion):
    monkeypatch.setenv("TTS_ENGINE", "custom_voice")
    eng = engines.Engines.from_env()
    m = _FakeCustomVoiceModel(_chunks())
    eng._model = m
    list(eng.stream_pcm("привет", emotion=emotion))
    assert m.calls[-1]["instruct"] is None


def test_custom_voice_instruct_map_covers_the_shared_enum(monkeypatch):
    # Every non-neutral value the agent can send must have a usable instruct
    # clause; neutral maps to empty (→ None). Mirrors motion_events.EMOTIONS.
    shared_enum = (
        "neutral", "happy", "sad", "excited", "calm",
        "serious", "surprised", "angry", "tender", "thinking",
    )
    assert set(engines.CUSTOMVOICE_EMOTION_INSTRUCT) == set(shared_enum)
    assert engines.CUSTOMVOICE_EMOTION_INSTRUCT["neutral"] == ""
    assert all(
        engines.CUSTOMVOICE_EMOTION_INSTRUCT[e] for e in shared_enum if e != "neutral"
    )


# --------------------------------------------------------------------------- #
# VoiceCloneEngine
# --------------------------------------------------------------------------- #
def test_voice_clone_simple_one_profile(monkeypatch, tmp_path):
    wav = tmp_path / "ref.wav"
    wav.write_bytes(b"fake")
    monkeypatch.setenv("TTS_ENGINE", "voice_clone")
    monkeypatch.setenv("TTS_REF_AUDIO", str(wav))
    monkeypatch.setenv("TTS_REF_TEXT", "привет мир")
    eng = engines.Engines.from_env()
    assert list(eng.refs) == ["default"]
    m = _FakeVoiceCloneModel(_chunks())
    eng._model = m
    list(eng.stream_pcm("test"))
    assert m.calls[-1]["ref_audio"] == str(wav)
    assert m.calls[-1]["ref_text"] == "привет мир"
    assert m.calls[-1]["language"] == "Russian"


def test_voice_clone_multi_profile_named(monkeypatch, tmp_path):
    a = tmp_path / "p.wav"; a.write_bytes(b"a")
    b = tmp_path / "m.wav"; b.write_bytes(b"m")
    monkeypatch.setenv("TTS_ENGINE", "voice_clone")
    monkeypatch.setenv("TTS_VOICE_REFS", "pasha,mama")
    monkeypatch.setenv("TTS_REF_pasha_AUDIO", str(a))
    monkeypatch.setenv("TTS_REF_pasha_TEXT", "текст паши")
    monkeypatch.setenv("TTS_REF_mama_AUDIO", str(b))
    monkeypatch.setenv("TTS_REF_mama_TEXT", "текст мамы")
    eng = engines.Engines.from_env()
    assert set(eng.refs) == {"pasha", "mama"}
    m = _FakeVoiceCloneModel(_chunks())
    eng._model = m
    list(eng.stream_pcm("x", voice="mama"))
    assert m.calls[-1]["ref_audio"] == str(b)
    assert m.calls[-1]["ref_text"] == "текст мамы"


def test_voice_clone_default_resolves_to_first(monkeypatch, tmp_path):
    a = tmp_path / "p.wav"; a.write_bytes(b"a")
    b = tmp_path / "m.wav"; b.write_bytes(b"m")
    monkeypatch.setenv("TTS_ENGINE", "voice_clone")
    monkeypatch.setenv("TTS_VOICE_REFS", "pasha,mama")
    monkeypatch.setenv("TTS_REF_pasha_AUDIO", str(a))
    monkeypatch.setenv("TTS_REF_pasha_TEXT", "p")
    monkeypatch.setenv("TTS_REF_mama_AUDIO", str(b))
    monkeypatch.setenv("TTS_REF_mama_TEXT", "m")
    eng = engines.Engines.from_env()
    m = _FakeVoiceCloneModel(_chunks())
    eng._model = m
    list(eng.stream_pcm("x", voice="default"))
    assert m.calls[-1]["ref_audio"] == str(a)  # first profile


def test_voice_clone_unknown_profile_raises(monkeypatch, tmp_path):
    wav = tmp_path / "ref.wav"; wav.write_bytes(b"x")
    monkeypatch.setenv("TTS_ENGINE", "voice_clone")
    monkeypatch.setenv("TTS_REF_AUDIO", str(wav))
    monkeypatch.setenv("TTS_REF_TEXT", "привет")
    eng = engines.Engines.from_env()
    eng._model = _FakeVoiceCloneModel(_chunks())
    with pytest.raises(RuntimeError, match="unknown ref profile"):
        list(eng.stream_pcm("x", voice="nope"))


def test_voice_clone_missing_refs_raises(monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "voice_clone")
    with pytest.raises(RuntimeError, match="requires reference audio"):
        engines.Engines.from_env()


def test_voice_clone_missing_audio_file_raises(monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "voice_clone")
    monkeypatch.setenv("TTS_REF_AUDIO", "/no/such/file.wav")
    monkeypatch.setenv("TTS_REF_TEXT", "x")
    with pytest.raises(RuntimeError, match="audio file not found"):
        engines.Engines.from_env()


def test_voice_clone_incomplete_profile_raises(monkeypatch, tmp_path):
    a = tmp_path / "p.wav"; a.write_bytes(b"a")
    monkeypatch.setenv("TTS_ENGINE", "voice_clone")
    monkeypatch.setenv("TTS_VOICE_REFS", "pasha")
    monkeypatch.setenv("TTS_REF_pasha_AUDIO", str(a))
    # TTS_REF_pasha_TEXT missing
    with pytest.raises(RuntimeError, match="incomplete"):
        engines.Engines.from_env()


# --------------------------------------------------------------------------- #
# VoiceDesignEngine
# --------------------------------------------------------------------------- #
def test_voice_design_uses_configured_instruct(monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "voice_design")
    monkeypatch.setenv("TTS_INSTRUCT", "warm baritone")
    eng = engines.Engines.from_env()
    m = _FakeVoiceDesignModel(_chunks())
    eng._model = m
    list(eng.stream_pcm("text"))
    assert m.calls[-1]["instruct"] == "warm baritone"


def test_voice_design_overrides_instruct_via_voice(monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "voice_design")
    monkeypatch.setenv("TTS_INSTRUCT", "default instruct")
    eng = engines.Engines.from_env()
    m = _FakeVoiceDesignModel(_chunks())
    eng._model = m
    list(eng.stream_pcm("text", voice="bright cheerful female"))
    assert m.calls[-1]["instruct"] == "bright cheerful female"


def test_voice_design_missing_instruct_raises(monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "voice_design")
    eng = engines.Engines.from_env()
    eng._model = _FakeVoiceDesignModel(_chunks())
    with pytest.raises(RuntimeError, match="requires an instruction"):
        list(eng.stream_pcm("x"))


# --------------------------------------------------------------------------- #
# synthesize facade
# --------------------------------------------------------------------------- #
def test_facade_is_loaded(monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "custom_voice")
    assert synthesize.is_loaded() is False
    synthesize._engine = engines.Engines.from_env()
    synthesize._engine._model = object()
    assert synthesize.is_loaded() is True


def test_facade_stream_delegates(monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "custom_voice")
    m = _FakeCustomVoiceModel([(np.array([0.0, 1.0], dtype=np.float32), 24000)])
    synthesize._engine = engines.Engines.from_env()
    synthesize._engine._model = m
    out = list(synthesize.stream_pcm("hi"))
    assert len(out) == 1
    assert list(np.frombuffer(out[0], dtype="<i2")) == [0, 32767]


def test_facade_backcompat_attrs(monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "custom_voice")
    monkeypatch.setenv("TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")
    monkeypatch.setenv("TTS_SPEAKER", "aiden")
    synthesize._engine = engines.Engines.from_env()
    assert synthesize.MODEL_NAME == "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
    assert synthesize.SPEAKER == "aiden"
    assert synthesize.LANGUAGE == "Russian"


def test_facade_engine_accessor(monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "voice_design")
    monkeypatch.setenv("TTS_INSTRUCT", "x")
    assert synthesize.engine().name == "voice_design"