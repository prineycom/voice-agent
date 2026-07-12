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


def _read_persisted() -> str | None:
    """The explicitly-persisted Voice, or ``None`` if unset/invalid/unreadable.

    Reads the tiny state file fresh on every call — no cache — so a switch is
    always picked up on the next utterance regardless of filesystem mtime
    granularity (a stat/mtime cache silently misses same-mtime writes on coarse
    filesystems). The file is a few bytes, so the per-sentence read is negligible
    and the OS page cache absorbs it. Any error → ``None`` (treated as unset).
    """
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    name = data.get("voice") if isinstance(data, dict) else None
    return name if is_valid(name) else None


def get_active_voice() -> str:
    """The active Voice for the GET/UI: the persisted value, or the default.

    Always yields a concrete preset (the selector shows a highlighted active
    voice even before the first switch).
    """
    return _read_persisted() or default_voice()


def active_voice_or_none() -> str | None:
    """The wire value the TTS plugin sends: the persisted preset, or ``None``.

    ``None`` (nothing explicitly switched) lets the plugin fall back to its
    engine-agnostic constructor ``voice`` (``"default"``), so a system that has
    never used the switcher — or one rolled back to a non-CustomVoice engine —
    keeps sending ``"default"`` instead of a concrete preset only CustomVoice can
    resolve.
    """
    return _read_persisted()


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
    return name


def state() -> dict:
    """The GET payload: the active Voice + the catalog flagged with `active`."""
    active = get_active_voice()
    return {
        "active": active,
        "voices": [{"id": v, "active": v == active} for v in VOICES],
    }
