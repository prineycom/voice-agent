"""Room-global Dormant/Active wake-word state machine (ADR-0021).

One agent = one attention: a single `WakeState` per session holds whether the
agent is **Dormant** (user speech ignored, heavy GPU STT gated off) or **Active**
(speech processed normally). The audio classifier (`wake_detector`) flips it to
Active on a wake-word hit; a silence timeout returns it to Dormant. Transitions
are published on the `voiceagent` UI channel so the frontend can play the
Activation signal (#60).

The class concentrates all the gating decisions so the framework wiring in
`agent.py` stays thin:

- ``should_transcribe()`` — the STT gate: while Dormant, the plugin skips the
  Desktop GPU round-trip entirely (only the cheap Pi-side classifier runs).
- ``filter_transcript(raw)`` — strips the wake word from a finalized transcript
  (one-breath ``«Приней, сколько времени»`` → ``«сколько времени»``) for BOTH the
  displayed transcript and the LLM.
- ``should_drop_turn(text)`` — after STT, decides whether the turn reaches the LLM
  (drop a bare wake word or any turn while Dormant).

Config:
- ``enabled`` (WAKEWORD_ENABLED, default on) gates the whole feature. When off,
  ``should_transcribe`` is always True and nothing is stripped/dropped — today's
  always-listening behavior.
- ``silence_timeout`` (default 8 s) — Active→Dormant after this much quiet (reset
  on every user OR agent turn boundary). This is the trivial auto-sleep of the
  core slice; the stop phrase and the strict follow-up mode arrive in #59.

Thread/loop model: every method runs on the agent's asyncio loop (the detector,
STT hooks and agent-state hooks all do). The silence timer uses ``loop.call_later``
and is a no-op when there is no running loop (pure-logic unit tests).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable

from wake_phrases import is_only_wake_word, strip_wake_word

log = logging.getLogger("agent")

STATE_ACTIVE = "active"
STATE_DORMANT = "dormant"

DEFAULT_SILENCE_TIMEOUT = 8.0


def wake_event_json(state: str) -> bytes:
    """Build the UTF-8 JSON payload for a wake transition on the data channel.

    Mirrors ``motion_events.motion_event_json`` but with ``type: "wake"`` so it
    never collides with ``motion`` on the shared ``voiceagent`` topic (#58/#60).
    """
    return json.dumps({"type": "wake", "state": state}).encode("utf-8")


class WakeState:
    def __init__(
        self,
        *,
        enabled: bool = True,
        silence_timeout: float = DEFAULT_SILENCE_TIMEOUT,
        publish: Callable[[bytes], None] | None = None,
    ) -> None:
        self.enabled = enabled
        self.silence_timeout = silence_timeout
        self._publish = publish
        self._active = False
        self._timer: asyncio.TimerHandle | None = None

    # -- queries ---------------------------------------------------------------
    @property
    def active(self) -> bool:
        """True when the agent is Active. Always effectively True when disabled."""
        return self._active if self.enabled else True

    def should_transcribe(self) -> bool:
        """Whether this turn should reach the Desktop GPU STT.

        Disabled → always (feature off). Enabled → only while Active. While
        Dormant the STT plugin skips the Desktop call, so no GPU is spent on
        speech that is not addressed to the agent — the whole point of the gate.
        The audio classifier still runs on the raw track regardless (that is how
        Dormant→Active happens), so a wake word during a Dormant utterance flips
        the state before the turn ends and this returns True for that same turn.
        """
        return True if not self.enabled else self._active

    # -- transitions -----------------------------------------------------------
    def on_wake_detected(self, name: str, score: float) -> None:
        """Flip to Active on a classifier hit (called by the wake detector)."""
        if not self.enabled:
            return
        if not self._active:
            log.info("wake word '%s' detected (%.2f) → ACTIVE", name, score)
        self._activate()

    def _activate(self) -> None:
        changed = not self._active
        self._active = True
        if changed and self._publish is not None:
            self._publish(wake_event_json(STATE_ACTIVE))
        self._arm_timer()

    def sleep(self, reason: str) -> None:
        """Return to Dormant (silence timeout)."""
        self._cancel_timer()
        if not self._active:
            return
        self._active = False
        log.info("agent → DORMANT (%s)", reason)
        if self._publish is not None:
            self._publish(wake_event_json(STATE_DORMANT))

    # -- transcript side (called from the STT plugin) --------------------------
    def filter_transcript(self, raw: str) -> str:
        """Strip the leading wake word for the display + LLM (one-breath)."""
        if not self.enabled:
            return raw
        return strip_wake_word(raw)

    # -- turn side (called from Agent.on_user_turn_completed) ------------------
    def should_drop_turn(self, text: str) -> bool:
        """Whether to drop this user turn instead of answering it.

        ``text`` is the already-stripped transcript. Dropped when the agent is
        Dormant (speech not addressed to it) or the turn was only a wake word
        (woke it, nothing to answer). A real request in Active mode is kept.
        """
        if not self.enabled:
            return False
        if not self._active:
            return True  # Dormant: ignore (STT was gated; text is empty anyway)
        self._note_activity()
        if not text.strip() or is_only_wake_word(text):
            return True  # woke with no request → stay Active, answer nothing
        return False

    def note_agent_activity(self) -> None:
        """Reset the silence timer on an agent turn boundary (it just spoke)."""
        if self.enabled and self._active:
            self._note_activity()

    # -- silence timer ---------------------------------------------------------
    def _note_activity(self) -> None:
        """A user or agent turn happened while Active: re-arm the silence timer."""
        self._arm_timer()

    def _arm_timer(self) -> None:
        self._cancel_timer()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop (unit test): timer disabled, transitions still work
        self._timer = loop.call_later(self.silence_timeout, self._on_timeout)

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _on_timeout(self) -> None:
        self._timer = None
        self.sleep(f"silence timeout ({self.silence_timeout:.0f}s)")
