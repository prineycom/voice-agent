"""Offline tests for voice_state — the persisted active-Voice global (ADR-0020 / #48).

Pure logic + a temp state file; no GPU/SFU/network. Each test points
VOICE_STATE_PATH at a tmp file and resets the module cache.
"""

import json

import pytest

import voice_state


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Fresh state file + clean cache/env per test."""
    monkeypatch.setenv("VOICE_STATE_PATH", str(tmp_path / "active-voice"))
    monkeypatch.delenv("VOICE_DEFAULT", raising=False)
    voice_state._cache["mtime"] = None
    voice_state._cache["value"] = None
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
    voice_state._cache["mtime"] = None
    assert voice_state.get_active_voice() == "ryan"


def test_get_falls_back_on_unknown_persisted_value(tmp_path, monkeypatch):
    path = tmp_path / "unknown"
    path.write_text(json.dumps({"voice": "eric"}), encoding="utf-8")
    monkeypatch.setenv("VOICE_STATE_PATH", str(path))
    voice_state._cache["mtime"] = None
    assert voice_state.get_active_voice() == "ryan"


def test_set_updates_get_immediately_same_process():
    # The write must invalidate this process's cache (the switcher process),
    # not only cross-process via mtime.
    voice_state.get_active_voice()  # prime the cache with the default
    voice_state.set_active_voice("sohee")
    assert voice_state.get_active_voice() == "sohee"


def test_state_payload_flags_active():
    voice_state.set_active_voice("serena")
    st = voice_state.state()
    assert st["active"] == "serena"
    active = [v["id"] for v in st["voices"] if v["active"]]
    assert active == ["serena"]
    assert {v["id"] for v in st["voices"]} == set(voice_state.VOICES)


def test_persists_across_reload(tmp_path, monkeypatch):
    """A new read (cache cleared, mimicking a restart) sees the persisted value."""
    voice_state.set_active_voice("ono_anna")
    voice_state._cache["mtime"] = None
    voice_state._cache["value"] = None
    assert voice_state.get_active_voice() == "ono_anna"
