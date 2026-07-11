# Plan — #51 Emotion tag → CustomVoice `instruct` (expanded enum)

**Issue:** https://github.com/prineycom/voice-agent/issues/51 (parent #49, blocked by #50)
**Source of truth:** `docs/adr/0020-customvoice-emotion-and-voice-switcher.md`

## Goal

(1) Expand the emotion enum 5 → 10, single source of truth, unknown → neutral.
(2) Static emotion → English `instruct` map engine-side; consume the `emotion` field the
agent already sends per sentence over WS — **wire protocol unchanged**. No intensity in v1.
Face rides A2E; the tag → A2F boost accepts the new values.

## Changes

1. `infra/pi/agent/motion_events.py` — `EMOTIONS` → `neutral, happy, sad, excited, calm,
   serious, surprised, angry, tender, thinking`. This is the shared SoT.
2. `infra/desktop/tts/engines.py` — `CUSTOMVOICE_EMOTION_INSTRUCT` map (English prosody
   clauses) + `CustomVoiceEngine._instruct_for(emotion)`; pass `instruct=` per sentence to
   `generate_custom_voice_streaming`. `neutral`/unknown/vector → `None` (plain speaker).
3. `infra/desktop/a2f/emotion.py` — `_ENUM_TO_A2E` boost weights for the 5 new values
   (calm/serious → `{}` = let A2E drive); unknown already → `{}`.
4. `infra/desktop/a2f/arkit.py` — doc-only `EMOTIONS` mirror → 10.
5. Tests: instruct sent for known emotion, `None` for neutral/unknown/vector, map covers
   the shared enum.

## Runaway guard (ADR-0020 watch-out)

`TTS_MAX_NEW_TOKENS` (wired in #50) caps a sentence's audio-token count. SOUL.md already
instructs "numbers as words" (defends the 8080/Python/FastAPI case). Default kept at the
library 2048; tune down after the live check of the emotion + number+latin sentences.

## Verification

- Desktop TTS unit tests (instruct mapping) + agent tests (enum) green.
- Live: each forced tag audibly shifts `ryan`'s delivery; no runaway; A2F still renders —
  epic consolidated deploy / human sign-off.
