"""Active-voice state — the agent worker's owned, persisted global Voice.

The **Voice** is *who* the agent sounds like: one of the Qwen3-TTS CustomVoice
preset speakers (ADR-0020 / #48). It is orthogonal to the emotion tag (*how* it
speaks). There is exactly ONE active Voice, a server-side global on the Pi that
survives restarts, changed via the voice-switcher HTTP endpoint (served by the
web front door, `infra/pi/web/server.py`) and consumed live by the TTS plugin —
it flows to the Desktop over the existing per-session ``voice`` field, so a
switch takes effect on the next utterance with no service restart / model reload
(one CustomVoice model stays loaded for every preset).

This module is the single authority: the preset catalog + the persistence. It is
imported by BOTH the worker (reads the active voice per sentence) and the web
server (serves GET/POST). Pure stdlib — no heavy imports — so the web server can
import it without pulling the agent's ML deps.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

# Switchable CustomVoice preset speakers (ADR-0020 / #48). `ryan` is first = the
# default. The CN-dialect `eric`/`dylan` are deliberately excluded for Russian.
VOICES: tuple[str, ...] = (
    "ryan", "aiden", "serena", "vivian", "ono_anna", "sohee", "uncle_fu",
)


def default_voice() -> str:
    """The fallback Voice when nothing is persisted yet (env-overridable)."""
    v = os.environ.get("VOICE_DEFAULT", "").strip()
    return v if v in VOICES else VOICES[0]


def _state_path() -> Path:
    """Where the active Voice is persisted on the Pi (env-overridable).

    Default under XDG state so it survives restarts and is writable by the
    service user shared by the worker and the web server.
    """
    raw = os.environ.get("VOICE_STATE_PATH", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".local" / "state" / "voice-agent" / "active-voice"


def is_valid(name: str | None) -> bool:
    """True if ``name`` is a known switchable preset."""
    return isinstance(name, str) and name.strip() in VOICES


def list_voices() -> list[str]:
    """The switchable preset catalog, in display order (default first)."""
    return list(VOICES)


# Cache the parsed value keyed by the file's mtime so the per-sentence read on the
# TTS hot path is a cheap stat, not a full read+parse every time. Guarded by a lock
# because the worker's async flushes may call get_active_voice concurrently.
_lock = threading.Lock()
_cache: dict[str, object] = {"mtime": None, "value": None}


def get_active_voice() -> str:
    """Return the persisted active Voice, or the default if unset/invalid.

    Safe to call on the hot path (once per sentence): re-reads the state file only
    when its mtime changed, so a switch is picked up on the next utterance without
    a per-call disk parse. Any read/parse error falls back to the default — the
    agent must never go mute over a bad state file.
    """
    path = _state_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return default_voice()
    with _lock:
        if _cache["mtime"] == mtime and _cache["value"] is not None:
            return _cache["value"]  # type: ignore[return-value]
        value = _read_file(path)
        _cache["mtime"] = mtime
        _cache["value"] = value
        return value


def _read_file(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        name = data.get("voice") if isinstance(data, dict) else None
    except (OSError, ValueError):
        return default_voice()
    return name if is_valid(name) else default_voice()


def set_active_voice(name: str) -> str:
    """Validate + persist ``name`` as the active Voice; return the stored value.

    Atomic write (temp + replace) so a concurrent reader never sees a half-written
    file. Raises ``ValueError`` for an unknown preset (the caller maps it to 400).
    """
    if not is_valid(name):
        raise ValueError(
            f"unknown voice {name!r}; valid: {', '.join(VOICES)}"
        )
    name = name.strip()
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".active-voice.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"voice": name}, f)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    with _lock:  # invalidate cache so this process reflects the write immediately
        _cache["mtime"] = None
        _cache["value"] = None
    return name


def state() -> dict:
    """The GET payload: the active Voice + the catalog flagged with `active`."""
    active = get_active_voice()
    return {
        "active": active,
        "voices": [{"id": v, "active": v == active} for v in VOICES],
    }
