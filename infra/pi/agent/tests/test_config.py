"""Offline tests for config parsing — LLM defaults, env overrides, required keys.

No GPU/SFU/network: load_config() only reads env (and a possibly-absent .env).
"""

from pathlib import Path

import pytest

import config
from config import load_config


@pytest.fixture(autouse=True)
def _isolate_dotenv(monkeypatch):
    """Isolate config tests from the developer's on-disk .env.

    load_config() unconditionally calls load_dotenv() on the agent's .env, so a
    populated local file (e.g. LLM_BASE_URL=127.0.0.1, LLM_MODEL=...) would leak
    into these default/override tests and make them machine-dependent. Neutralise
    it so the tests see only the env they set via monkeypatch.
    """
    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: False)


def test_llm_defaults(monkeypatch):
    """With LiveKit keys set, load_config returns the documented LLM defaults."""
    monkeypatch.setenv("LIVEKIT_API_KEY", "devkey")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "devsecret")
    for key in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "SOUL_PATH"):
        monkeypatch.delenv(key, raising=False)

    cfg = load_config()

    assert cfg.llm_base_url == "http://localhost:4000/v1"
    assert cfg.llm_model == "voice-agent"
    assert cfg.llm_api_key == "litellm-local"
    assert cfg.llm_reasoning_effort == "none"
    assert str(cfg.soul_path).endswith("SOUL.md")


def test_env_overrides(monkeypatch):
    """Env overrides propagate; soul_path is a pathlib.Path."""
    monkeypatch.setenv("LIVEKIT_API_KEY", "devkey")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "devsecret")
    monkeypatch.setenv("LLM_BASE_URL", "http://example.test:9999/v1")
    monkeypatch.setenv("LLM_MODEL", "custom-model")
    monkeypatch.setenv("LLM_API_KEY", "custom-key")
    monkeypatch.setenv("LLM_REASONING_EFFORT", "low")
    monkeypatch.setenv("SOUL_PATH", "/tmp/custom-soul.md")

    cfg = load_config()

    assert cfg.llm_base_url == "http://example.test:9999/v1"
    assert cfg.llm_model == "custom-model"
    assert cfg.llm_api_key == "custom-key"
    assert cfg.llm_reasoning_effort == "low"
    assert isinstance(cfg.soul_path, Path)
    assert cfg.soul_path == Path("/tmp/custom-soul.md")


def test_missing_livekit_keys_raise(monkeypatch):
    """Regression: missing required LiveKit keys still fails loud.

    Neutralize the real `.env` load (which carries LiveKit keys) so the
    delenv'd environment is what load_config actually validates.
    """
    monkeypatch.setattr("config.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)

    with pytest.raises(RuntimeError):
        load_config()
