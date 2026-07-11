"""Offline tests for voice_state — the persisted active-Voice global (ADR-0020 / #48).

Pure logic + a temp state file; no GPU/SFU/network. Each test points
VOICE_STATE_PATH at a fresh tmp file (state is read fresh on every call).
"""

import json

import pytest

import voice_state


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Fresh state file + clean env per test."""
    monkeypatch.setenv("VOICE_STATE_PATH", str(tmp_path / "active-voice"))
    monkeypatch.delenv("VOICE_DEFAULT", raising=False)
    yield


def test_catalog_excludes_cn_dialect_speakers():
    assert voice_state.list_voices()[0] == "ryan"  # default first
    for bad in ("eric", "dylan"):
        assert bad not in voice_state.VOICES


def test_default_when_unset():
    assert voice_state.get_active_voice() == "ryan"


def test_default_override_via_env(monkeypatch):
    monkeypatch.setenv("VOICE_DEFAULT", "aiden")
    assert voice_state.default_voice() == "aiden"
    assert voice_state.get_active_voice() == "aiden"


def test_default_override_ignores_unknown(monkeypatch):
    monkeypatch.setenv("VOICE_DEFAULT", "nope")
    assert voice_state.default_voice() == "ryan"


@pytest.mark.parametrize("name", ["aiden", "serena", "uncle_fu"])
def test_set_then_get_roundtrip(name):
    assert voice_state.set_active_voice(name) == name
    assert voice_state.get_active_voice() == name


def test_set_invalid_raises_and_does_not_persist():
    with pytest.raises(ValueError):
        voice_state.set_active_voice("eric")
    with pytest.raises(ValueError):
        voice_state.set_active_voice(None)
    # nothing persisted → still the default
    assert voice_state.get_active_voice() == "ryan"


def test_set_strips_whitespace():
    assert voice_state.set_active_voice("  vivian  ") == "vivian"
    assert voice_state.get_active_voice() == "vivian"


def test_get_falls_back_on_corrupt_file(tmp_path, monkeypatch):
    path = tmp_path / "corrupt"
    path.write_text("not json", encoding="utf-8")
    monkeypatch.setenv("VOICE_STATE_PATH", str(path))
    assert voice_state.get_active_voice() == "ryan"


def test_get_falls_back_on_unknown_persisted_value(tmp_path, monkeypatch):
    path = tmp_path / "unknown"
    path.write_text(json.dumps({"voice": "eric"}), encoding="utf-8")
    monkeypatch.setenv("VOICE_STATE_PATH", str(path))
    assert voice_state.get_active_voice() == "ryan"


def test_set_updates_get_immediately_same_process():
    voice_state.get_active_voice()  # read once (default) before the switch
    voice_state.set_active_voice("sohee")
    assert voice_state.get_active_voice() == "sohee"


def test_state_payload_flags_active():
    voice_state.set_active_voice("serena")
    st = voice_state.state()
    assert st["active"] == "serena"
    active = [v["id"] for v in st["voices"] if v["active"]]
    assert active == ["serena"]
    assert {v["id"] for v in st["voices"]} == set(voice_state.VOICES)


def test_active_or_none_is_none_when_unpersisted():
    # The wire value: no explicit switch → None so the plugin sends its
    # engine-agnostic constructor "default" (works on any engine, incl. rollback).
    assert voice_state.active_voice_or_none() is None


def test_active_or_none_returns_persisted_after_switch():
    voice_state.set_active_voice("aiden")
    assert voice_state.active_voice_or_none() == "aiden"


def test_active_or_none_is_none_on_corrupt_file(tmp_path, monkeypatch):
    path = tmp_path / "corrupt2"
    path.write_text("garbage", encoding="utf-8")
    monkeypatch.setenv("VOICE_STATE_PATH", str(path))
    assert voice_state.active_voice_or_none() is None
    # ...while the UI-facing getter still yields a concrete default.
    assert voice_state.get_active_voice() == "ryan"


def test_persists_across_reload():
    """The persisted value survives (file-backed, read fresh each call)."""
    voice_state.set_active_voice("ono_anna")
    assert voice_state.get_active_voice() == "ono_anna"
