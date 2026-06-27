"""Emotion enum, streaming emotion-tag stripper, and motion-event JSON builder.

The LLM emits inline emotion tags like ``[emotion:happy]`` in its reply. Before the
streamed text reaches TTS or the transcript, those tags must be stripped out (and
turned into motion/expression events published on the ``voiceagent`` data channel).

This module is pure logic — no framework calls — so it is trivially unit-testable:
- ``normalize_emotion`` clamps an arbitrary string to the known emotion enum.
- ``motion_event_json`` builds the data-channel payload for a motion event.
- ``EmotionTagStripper`` removes tags from text arriving in arbitrary chunks, where
  a single tag may be split across ``feed()`` calls.
"""

from __future__ import annotations

import json
import re
from typing import Callable

EMOTIONS = ("neutral", "happy", "sad", "surprised", "thinking")
DEFAULT_EMOTION = "neutral"

# Matches a complete inline emotion tag, e.g. "[emotion:happy]" or "[emotion: Sad ]".
_TAG_RE = re.compile(r"\[emotion:\s*([a-zA-Z]+)\s*\]", re.IGNORECASE)

# Literal start of an open (not-yet-closed) tag, used to decide what to hold back.
_OPEN_PREFIX = "[emotion:"


def normalize_emotion(name: str | None) -> str:
    """Clamp an arbitrary emotion name to the known enum.

    Lowercases and strips ``name``; returns it when it is a known emotion, else the
    default ``"neutral"`` (also for None, empty, or unknown values).
    """
    if not name:
        return DEFAULT_EMOTION
    candidate = name.strip().lower()
    return candidate if candidate in EMOTIONS else DEFAULT_EMOTION


def motion_event_json(state: str, emotion: str) -> bytes:
    """Build the UTF-8 JSON payload for a motion event on the data channel."""
    return json.dumps(
        {"type": "motion", "state": state, "emotion": normalize_emotion(emotion)}
    ).encode("utf-8")


class EmotionTagStripper:
    """Streaming, stateful remover of ``[emotion:xxx]`` tags from chunked text.

    Text arrives via ``feed()`` in arbitrary chunks; a tag may be split across calls
    (e.g. ``"[emo"`` then ``"tion:happy]"``). Each complete tag is removed, its
    normalized emotion recorded (and passed to ``on_emotion``), and the cleaned text
    is emitted as soon as it is safe — i.e. everything except a trailing suffix that
    could still be the beginning of an incomplete tag is returned. The emitted text
    never contains the substring ``"[emotion"``.
    """

    def __init__(self, on_emotion: Callable[[str], None] | None = None) -> None:
        self._on_emotion = on_emotion
        self._buffer = ""
        self._emotions: list[str] = []

    def pop_emotions(self) -> list[str]:
        """Return and clear the emotions recorded since the last call."""
        drained = self._emotions
        self._emotions = []
        return drained

    def _record(self, raw: str) -> None:
        emotion = normalize_emotion(raw)
        self._emotions.append(emotion)
        if self._on_emotion is not None:
            self._on_emotion(emotion)

    def _strip_complete_tags(self) -> None:
        """Remove every complete tag currently in the buffer, recording each."""

        def _replace(match: re.Match[str]) -> str:
            self._record(match.group(1))
            return ""

        self._buffer = _TAG_RE.sub(_replace, self._buffer)

    @staticmethod
    def _is_partial_open_tag(tail: str) -> bool:
        """True if ``tail`` (starting at a ``'['``) could still grow into a tag.

        Two cases, neither of which has a closing ``']'`` yet:
        - the tail is a prefix of ``"[emotion:"`` itself (e.g. ``"["``, ``"[emo"``);
        - the tail already includes ``"[emotion:"`` and the emotion word is still
          streaming (e.g. ``"[emotion:ha"``).
        A ``'['`` that diverges from ``"[emotion:"`` (e.g. ``"[abc"``) is NOT partial,
        so it is emitted rather than held back forever.
        """
        if "]" in tail:
            return False
        low = tail.lower()
        return _OPEN_PREFIX.startswith(low) or low.startswith(_OPEN_PREFIX)

    def feed(self, text: str) -> str:
        """Append ``text``, strip complete tags, and emit the longest safe prefix.

        The held-back tail is the run from the last ``'['`` to the end of the buffer
        when that run could still become a tag (see ``_is_partial_open_tag``); it
        stays in the buffer for the next ``feed``/``flush``. Returns the cleaned,
        tag-free emitted text (never containing ``"[emotion"``).
        """
        self._buffer += text
        self._strip_complete_tags()

        idx = self._buffer.rfind("[")
        if idx != -1 and self._is_partial_open_tag(self._buffer[idx:]):
            emitted = self._buffer[:idx]
            self._buffer = self._buffer[idx:]
        else:
            emitted = self._buffer
            self._buffer = ""
        return emitted

    def flush(self) -> str:
        """Emit and clear whatever remains in the buffer.

        Strips a trailing complete tag once more (in case one just completed), then
        returns the rest verbatim — a dangling ``"[..."`` that never closed is
        emitted as-is. Clears the buffer.
        """
        self._strip_complete_tags()
        remaining = self._buffer
        self._buffer = ""
        return remaining
