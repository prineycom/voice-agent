"""Wake-word / stop-phrase text logic — pure, framework-free, unit-testable.

The audio classifier (wake_detector) is what flips the agent Dormant→Active; this
module handles the *transcript* side of wake-word activation (ADR-0021):

- ``strip_wake_word`` removes a leading wake word from a finalized transcript so a
  one-breath ``«Приней, сколько времени»`` reaches the LLM and the displayed
  transcript as just ``«сколько времени»`` (#58).
- ``is_stop_phrase`` recognizes ``{wake} + стоп`` adjacent (any of the three names)
  so ``«Приней, стоп»`` sleeps the agent — while a bare ``«стоп»`` does not (#59).

Matching is deliberately lenient about the exact spelling the STT returns for the
short Russian names: faster-whisper transcribes ``«хей джарвис»`` variably
(``«эй джарвис»``, ``«хэй джарвис»``…) and may drop/added punctuation, so the
patterns accept common variants. The set is kept in sync with the wake words the
classifier is trained on (``infra/desktop/wakeword/configs/prinei-prod.yaml``).
"""

from __future__ import annotations

import re

# Canonical wake words (display form) — the three from ADR-0021.
WAKE_WORDS: tuple[str, ...] = ("Приней", "Приня", "хей джарвис")

# Transcript-side alternation, tolerant of STT spelling variants for the short,
# non-English names. Longer alternatives first so "хей джарвис" wins over a bare
# "джарвис" would (a bare surname must NOT match — it is not a wake word).
#   - Приней / Приня + а few phonetic neighbours whisper emits (принэй, приней…)
#   - хей|хэй|эй + джарвис (the «хей» is the velar-Х the model is soft on)
_WAKE_ALT = r"(?:х[эе]?й\s+джарвис|эй\s+джарвис|принэй|приней|приня)"

# Leading wake word + trailing punctuation/space (one-breath prefix to strip).
_LEADING_RE = re.compile(rf"^\s*{_WAKE_ALT}\s*[,.!?…—\-]*\s*", re.IGNORECASE)

# {wake} immediately followed by «стоп» (adjacent, punctuation/space allowed
# between). Anchored on the wake word so a bare «стоп» never matches.
_STOP_RE = re.compile(rf"{_WAKE_ALT}\s*[,.!?…—\-]*\s*стоп\b", re.IGNORECASE)

# A transcript that is ONLY a wake word (nothing to answer) — user just woke it.
_ONLY_WAKE_RE = re.compile(rf"^\s*{_WAKE_ALT}\s*[,.!?…—\-]*\s*$", re.IGNORECASE)


def strip_wake_word(text: str) -> str:
    """Remove a single leading wake word (+ trailing punctuation) from ``text``.

    Returns ``text`` unchanged when it does not start with a wake word. Only the
    leading occurrence is stripped: a one-breath command puts the wake word first
    (``«Приней, сколько времени»`` → ``«сколько времени»``); a wake word later in
    a sentence is left alone. Interior whitespace of the remainder is preserved
    apart from the leading run consumed with the wake word.
    """
    return _LEADING_RE.sub("", text, count=1)


def is_only_wake_word(text: str) -> bool:
    """True if ``text`` is nothing but a wake word (e.g. the user just said «Приней»).

    Such a turn wakes the agent but carries no request, so the caller answers
    nothing (stays Active, silent) rather than sending an empty prompt to the LLM.
    """
    return bool(_ONLY_WAKE_RE.match(text))


def is_stop_phrase(text: str) -> bool:
    """True if ``text`` contains ``{wake} + стоп`` adjacent (the Stop phrase).

    Matches «Приней, стоп» / «хей джарвис стоп» / «Приня стоп». A bare «стоп»
    (no wake word) returns False — stopping requires naming the agent, so an
    incidental «стоп» mid-conversation never sleeps it (ADR-0021).
    """
    return bool(_STOP_RE.search(text))
