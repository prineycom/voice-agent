"""Offline tests for SOUL loading (_load_soul).

Importing `agent` pulls in `from livekit.plugins import openai, silero`; that
imports cleanly in the venv and touches no GPU/SFU/network at import time.
"""

from pathlib import Path

import pytest

from agent import _load_soul, _load_text_file

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


def test_unreadable_file_raises_actionable_error(tmp_path):
    """A file that exists but can't be read (PermissionError) raises RuntimeError,
    not an opaque traceback — same actionable discipline as a missing SOUL."""
    import os
    unreadable = tmp_path / "unreadable.md"
    unreadable.write_text("x", encoding="utf-8")
    os.chmod(unreadable, 0o000)
    try:
        with pytest.raises(RuntimeError, match="could not be read"):
            _load_text_file(unreadable, what="SOUL", required=True)
    finally:
        os.chmod(unreadable, 0o644)  # restore so tmp cleanup works


def test_directory_path_raises_actionable_error(tmp_path):
    """Pointing *_PATH at a directory raises a clear RuntimeError, not IsADirectoryError."""
    with pytest.raises(RuntimeError, match="could not be read"):
        _load_text_file(tmp_path, what="SOUL", required=True)


def test_optional_missing_file_returns_none_and_warns(tmp_path):
    """required=False on a missing file warns and returns None (worker skill path)."""
    out = _load_text_file(tmp_path / "missing_skill.md", what="Worker skill", required=False)
    assert out is None
