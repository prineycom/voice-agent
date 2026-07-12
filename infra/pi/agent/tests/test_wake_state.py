"""Tests for WakeState — the Dormant/Active machine, gating, and silence timer.

Pure logic + one async test for the silence timeout. No GPU/SFU/audio: the
classifier's role (flipping to Active) is simulated by calling on_wake_detected.
"""

import asyncio
import json

import pytest

from wake_state import STATE_ACTIVE, STATE_DORMANT, WakeState, wake_event_json


def _collect():
    events = []
    return events, events.append


# -- wake_event_json ----------------------------------------------------------
def test_wake_event_json_shape():
    assert json.loads(wake_event_json(STATE_ACTIVE)) == {"type": "wake", "state": "active"}
    assert json.loads(wake_event_json(STATE_DORMANT)) == {"type": "wake", "state": "dormant"}


# -- disabled (feature off = always listening) --------------------------------
def test_disabled_always_transcribes_and_passthrough():
    ws = WakeState(enabled=False)
    assert ws.should_transcribe() is True
    assert ws.active is True
    assert ws.filter_transcript("Приней, привет") == "Приней, привет"  # not stripped
    assert ws.should_drop_turn("что угодно") is False
    ws.on_wake_detected("hey_jarvis", 0.9)  # no-op when disabled


# -- enabled: gating ----------------------------------------------------------
def test_dormant_gates_stt():
    ws = WakeState(enabled=True)
    assert ws.active is False
    assert ws.should_transcribe() is False  # Dormant → no Desktop GPU STT


def test_wake_activates_and_publishes():
    events, pub = _collect()
    ws = WakeState(enabled=True, publish=pub)
    ws.on_wake_detected("hey_jarvis", 0.8)
    assert ws.active is True
    assert ws.should_transcribe() is True
    assert [json.loads(e)["state"] for e in events] == ["active"]


def test_announce_publishes_current_state():
    # A fresh session announces Dormant so the UI badge shows «спит» on connect.
    events, pub = _collect()
    ws = WakeState(enabled=True, publish=pub)
    ws.announce()
    assert [json.loads(e)["state"] for e in events] == ["dormant"]
    ws.on_wake_detected("hey_jarvis", 0.8)
    ws.announce()  # now Active
    assert json.loads(events[-1])["state"] == "active"


def test_announce_noop_when_disabled():
    events, pub = _collect()
    WakeState(enabled=False, publish=pub).announce()
    assert events == []


def test_repeated_wake_publishes_once():
    events, pub = _collect()
    ws = WakeState(enabled=True, publish=pub)
    ws.on_wake_detected("hey_jarvis", 0.8)
    ws.on_wake_detected("hey_jarvis", 0.9)  # already Active → no second publish
    assert [json.loads(e)["state"] for e in events] == ["active"]


# -- transcript filter + one-breath ------------------------------------------
def test_filter_strips_wake_when_active():
    ws = WakeState(enabled=True)
    ws.on_wake_detected("hey_jarvis", 0.8)
    assert ws.filter_transcript("Приней, сколько времени") == "сколько времени"


def test_stop_phrase_blanks_and_sleeps_immediately():
    events, pub = _collect()
    ws = WakeState(enabled=True, publish=pub)
    ws.on_wake_detected("hey_jarvis", 0.8)
    assert ws.filter_transcript("Приней, стоп") == ""      # blanked from display...
    assert ws.active is False                              # ...and asleep AT FILTER TIME
    # (not deferred: an empty-text turn may never reach on_user_turn_completed)
    assert ws.should_drop_turn("") is True                 # and the turn is dropped
    assert [json.loads(e)["state"] for e in events] == ["active", "dormant"]


def test_bare_stop_does_not_sleep():
    ws = WakeState(enabled=True)
    ws.on_wake_detected("hey_jarvis", 0.8)
    assert ws.filter_transcript("стоп") == "стоп"          # not a stop phrase
    # a bare «стоп» is a normal (if odd) request → answered, still Active
    assert ws.should_drop_turn("стоп") is False
    assert ws.active is True


# -- turn dropping ------------------------------------------------------------
def test_dormant_turn_dropped():
    ws = WakeState(enabled=True)
    # Dormant: even if some text arrived, drop it (not addressed to the agent).
    assert ws.should_drop_turn("случайная речь") is True


def test_bare_wake_word_turn_dropped_but_stays_active():
    ws = WakeState(enabled=True)
    ws.on_wake_detected("hey_jarvis", 0.8)
    # User just said «Приней» — woke it, nothing to answer.
    assert ws.should_drop_turn("") is True
    assert ws.active is True


def test_real_request_kept_in_followup():
    ws = WakeState(enabled=True, followup=True)
    ws.on_wake_detected("hey_jarvis", 0.8)
    assert ws.should_drop_turn("сколько времени") is False
    assert ws.active is True  # conversation window stays open (barge-in needs no wake)


def test_strict_mode_sleeps_after_each_answer():
    events, pub = _collect()
    ws = WakeState(enabled=True, followup=False, publish=pub)
    ws.on_wake_detected("hey_jarvis", 0.8)
    assert ws.should_drop_turn("сколько времени") is False  # answered...
    assert ws.active is False                               # ...then Dormant (strict)
    # Next turn needs a wake word again — even a barge-in requires one in strict.
    assert ws.should_transcribe() is False
    assert [json.loads(e)["state"] for e in events] == ["active", "dormant"]


def test_strict_mode_bare_wake_still_sleeps():
    # Regression: a lone wake word in strict mode must NOT leave a lingering Active
    # window (there is no silence timer in strict mode to rescue it).
    ws = WakeState(enabled=True, followup=False)
    ws.on_wake_detected("hey_jarvis", 0.8)
    assert ws.should_drop_turn("") is True   # bare wake, nothing to answer → dropped
    assert ws.active is False                # and asleep — not stuck Active forever


# -- silence timer ------------------------------------------------------------
@pytest.mark.asyncio
async def test_silence_timeout_sleeps():
    events, pub = _collect()
    ws = WakeState(enabled=True, followup=True, silence_timeout=0.05, publish=pub)
    ws.on_wake_detected("hey_jarvis", 0.8)
    assert ws.active is True
    await asyncio.sleep(0.12)
    assert ws.active is False
    assert [json.loads(e)["state"] for e in events] == ["active", "dormant"]


@pytest.mark.asyncio
async def test_activity_resets_timer():
    ws = WakeState(enabled=True, followup=True, silence_timeout=0.08)
    ws.on_wake_detected("hey_jarvis", 0.8)
    for _ in range(3):
        await asyncio.sleep(0.05)
        ws.note_agent_activity()  # keep resetting before expiry
    assert ws.active is True
    await asyncio.sleep(0.12)  # now let it expire
    assert ws.active is False


@pytest.mark.asyncio
async def test_strict_mode_has_no_persistent_timer():
    ws = WakeState(enabled=True, followup=False, silence_timeout=0.05)
    ws.on_wake_detected("hey_jarvis", 0.8)
    # Strict mode: no conversation window, so the timer never auto-sleeps here;
    # sleep happens on the answered turn instead (tested above).
    await asyncio.sleep(0.1)
    assert ws.active is True
