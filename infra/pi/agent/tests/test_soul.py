"""Offline tests for SOUL loading (_load_soul).

Importing `agent` pulls in `from livekit.plugins import openai, silero`; that
imports cleanly in the venv and touches no GPU/SFU/network at import time.
"""

from pathlib import Path

import pytest

from agent import _load_soul

SOUL_PATH = Path(__file__).resolve().parent.parent / "SOUL.md"


def test_loads_real_soul():
    """The real SOUL.md returns non-empty text with a known persona marker."""
    text = _load_soul(SOUL_PATH)
    assert text
    assert "Priney" in text or "русск" in text.lower()


def test_missing_path_raises():
    with pytest.raises(RuntimeError):
        _load_soul(Path("/nonexistent/path/to/SOUL.md"))


def test_empty_file_raises(tmp_path):
    empty = tmp_path / "empty_soul.md"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError):
        _load_soul(empty)
