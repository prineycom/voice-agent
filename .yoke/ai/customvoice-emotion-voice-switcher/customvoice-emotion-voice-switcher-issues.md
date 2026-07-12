# Issues — CustomVoice emotion-driven voice + voice switcher

Source of truth: `docs/adr/0020-customvoice-emotion-and-voice-switcher.md`, `.yoke/context.md`.
Parents: **#49** (Epic: Emotion-driven voice) for the emotion track; **#48** (UI voice switcher) for the switcher track.

| # | Slice | Type | Blocked by | Parent | URL |
|---|---|---|---|---|---|
| — | Epic: Emotion-driven voice via CustomVoice instruct | Feature | — | — | https://github.com/prineycom/voice-agent/issues/49 |
| 1 | Adopt CustomVoice speaker `ryan` as the production voice | AFK | — | #49 | https://github.com/prineycom/voice-agent/issues/50 |
| 2 | Emotion tag → CustomVoice `instruct` (expanded emotion enum) | AFK | #50 | #49 | https://github.com/prineycom/voice-agent/issues/51 |
| 3 | Expressive text + emotion tags in SOUL.md (make speech alive) | HITL | #51 | #49 | https://github.com/prineycom/voice-agent/issues/52 |
| 4 | Voice-switcher backend: HTTP endpoint + persisted global voice | AFK | #50 | #48 | https://github.com/prineycom/voice-agent/issues/53 |
| 5 | Voice-switcher frontend: voice selector in the UI | AFK | #53 | #48 | https://github.com/prineycom/voice-agent/issues/54 |

Dependency order: #50 → (#51 → #52) and (#53 → #54). #50 is the shared foundation for both tracks.

---

## #49 — Epic: Emotion-driven voice via CustomVoice instruct

Umbrella for making the agent's spoken Russian **alive** — per-utterance emotion driven by the LLM's
inline **Emotion tag**, mapped to a **Qwen3-TTS CustomVoice** `instruct` clause, on the default
**Voice** (CustomVoice speaker) `ryan`. Face keeps following the audio via **Audio2Emotion**;
aliveness also comes from the LLM writing expressive **text** (laughter, `ммм`, sighs). Children:
#50, #51, #52. Switcher half under #48.

## #50 — Adopt CustomVoice speaker `ryan` as the production voice (AFK, parent #49)

Flip prod TTS from `voice_clone` (Base/`pasha`) to `custom_voice`, default speaker `ryan`,
`language=Russian`, `temperature 0.8 / top_p 0.9`, no fixed seed. No emotion yet — proves the whole
pipeline runs end-to-end on CustomVoice and sounds good neutral. `voice_clone`/`voice_design` stay in
the registry. AC: engine/speaker flipped + deployed; live conversation in `ryan` (audio + A2F);
`/health` shows active engine/speaker; documented one-line rollback. Blocked by: none.

## #51 — Emotion tag → CustomVoice `instruct` (expanded emotion enum) (AFK, parent #49)

(1) Expand the emotion enum 5→~10 (`neutral, happy, sad, excited, calm, serious, surprised, angry,
tender, thinking`), single source of truth; unknown→`neutral`. (2) Static emotion→English `instruct`
map engine-side (like `VOXCPM_EMOTION_PROMPTS`); engine consumes the `emotion` field already sent per
sentence over WS — wire protocol unchanged. No intensity in v1. Face rides A2E; tag→A2F boost accepts
new values. AC: enum is SoT; each forced tag shifts `ryan`'s delivery; wire unchanged; no runaway on
long / number+latin sentences (clamp/normalize if needed); A2F still renders. Blocked by: #50.

## #52 — Expressive text + emotion tags in SOUL.md (make speech alive) (HITL, parent #49)

Teach the LLM (SOUL.md) to (1) write expressive text — interjections, laughter, sighs, lively
punctuation (spoken verbatim) — and (2) emit the expanded emotion tags. Subjective tuning against
`ryan` until natural, not overacted. AC: SOUL.md instructs both with examples; unforced conversations
show varied emotion + natural interjections; not overdone/spammed; tags stripped before transcript,
paralinguistic text kept; human listening sign-off. Blocked by: #51.

## #53 — Voice-switcher backend: HTTP endpoint + persisted global voice (AFK, parent #48)

Agent Worker owns the active Voice + a small HTTP API: `GET` voices (presets `ryan, aiden, serena,
vivian, ono_anna, sohee, uncle_fu`; exclude `eric`/`dylan`), `POST` voice (validate, set, persist on
Pi as one global surviving restart). Active speaker flows via the per-session `voice` field; switching
presets is instant (same model). Backend-only, curl-verifiable. AC: GET lists + flags active; POST
sets, next utterance uses it, invalid rejected; persists across restart; no service restart / reload.
Blocked by: #50.

## #54 — Voice-switcher frontend: voice selector in the UI (AFK, parent #48)

Voice selector in the shared Web/Kiosk frontend: lists voices from the backend GET (not hardcoded),
highlights active, POSTs choice; agent switches on next utterance; reflects persisted global on load.
Vanilla JS ES modules, no build step. AC: lists from backend + highlights active; choosing changes the
agent's sound next utterance; active reflected on load, consistent across browsers; no build step.
Blocked by: #53.
