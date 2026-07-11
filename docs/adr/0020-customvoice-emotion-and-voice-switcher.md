---
tags:
  - voice-agent
  - adr
status: accepted
relates-to: "0003-websocket-stt-tts, 0016-a2f-emotion-supply, 0018-voxcpm2-expressive-tts, 0019-cosyvoice2-russian-expressive-tts"
supersedes: "0018-voxcpm2-expressive-tts (emotion-to-voice path only)"
---

# Emotion-driven voice via Qwen3-TTS CustomVoice `instruct`, + UI voice switcher

We want the agent's spoken Russian to be **alive** — varied intonation, laughter, sighs, distinct
emotions — driven per-utterance by the LLM, the same way the hand-authored `ryan` samples sounded.
Prod is Qwen3-TTS `voice_clone` (the `pasha` clone): smooth but **emotionally flat**, and the
2026-07-11 investigation established *why* it cannot be fixed by prompting/params: on the **Base/clone**
model `instruct` is off-label — a weak conditioner swamped by sampling variance, so a fixed seed is a
lottery (robotic / wrong-emotion draws) and lower temperature does not tame it (much of the variance is
in the waveform decoder). ADR-0018 (VoxCPM2) and ADR-0019 (CosyVoice2) both failed to deliver an
expressive-Russian alternative in realtime on the 4070.

The same investigation found the answer on the **instruct-native** Qwen models: `VoiceDesign` and
especially **CustomVoice** (9 preset speakers) obey `instruct` as a trained first-class input — emotions
land, and the seed lottery nearly disappears (cheerful duration spread ~0.7 s across seeds vs ~2.5 s on
the clone). The user chose CustomVoice speaker **`ryan`** and approved the full emotion + phrase-type +
paralinguistics test.

## Decision

Adopt **Qwen3-TTS CustomVoice** as the emotion-capable voice path, default speaker **`ryan`**, and wire
the existing LLM emotion channel into it. Concretely:

1. **Emotion architecture — tag drives the VOICE; the face follows the audio.** The LLM's inline
   `[emotion:…]` tag maps to a CustomVoice `instruct` clause (the "how" of delivery). The **face** is
   produced by **Audio2Emotion** inferring from the now-expressive audio (per ADR-0016); the existing
   tag→A2F emotion *boost* is left in place as cheap reinforcement, not removed. This reverses ADR-0018's
   plan of mapping the tag to a VoxCPM2 style prompt.
2. **Emotion vocabulary expands to ~10** (was 5): `neutral, happy, sad, excited, calm, serious,
   surprised, angry, tender, thinking`. The face no longer constrains the set (it rides on A2E), so the
   vocabulary is chosen for what sounds good on CustomVoice. Single source of truth stays shared by
   SOUL.md, the agent, and (via the A2E boost) the avatar.
3. **`emotion → instruct` map lives in `CustomVoiceEngine`** on the Desktop (a static English-clause map,
   mirroring VoxCPM's `VOXCPM_EMOTION_PROMPTS`). The agent keeps sending the small enum tag over the WS
   `/tts` `emotion` field — **wire protocol unchanged**. Per-sentence granularity is kept (one WS
   request = one sentence = one instruct); the LLM changes the tag between sentences.
4. **Aliveness = tags + expressive TEXT.** Laughter (`ха-ха`), interjections (`ммм`, `ага`), sighs
   (`эх…`) and lively punctuation come from the LLM's *text*, not the tag. SOUL.md is updated to
   instruct the LLM to write expressively **and** emit emotion tags.
5. **Sampling:** `language="Russian"`, `temperature 0.8 / top_p 0.9`, **no fixed seed** (a global seed is
   a trap — freezes one draw). These are the env knobs already added to the engine.
6. **UI voice switcher (issue #48):** scope = **CustomVoice preset speakers only** (`ryan, aiden, serena,
   vivian, ono_anna, sohee, uncle_fu`; exclude the CN-dialect `eric`/`dylan`). One model stays loaded, so
   switching is instant with no restart and emotion works identically for every voice. The **agent owns
   the active voice** and exposes a small **HTTP endpoint** (`GET` voices / `POST` voice) from the agent
   worker; the selection is **persisted server-side on the Pi as one global voice** that survives restart.
   The active speaker flows to TTS via the existing per-session `voice` field (currently `TTS_VOICE`).
   `voice` = *who* (speaker), `emotion` = *how* (instruct) — orthogonal axes.

Not in v1: emotion **intensity** (low/med/high) — the instruct clause encodes a fixed moderate strength;
add later if needed. Cross-engine switching (clone / VoiceDesign) is deferred (would need model reload
and would make emotion inconsistent, since clones can't do it).

## Consequences

- **Trade-off: the agent's default voice is a preset (`ryan`), not Pavel's own cloned timbre.** Reliable
  per-utterance emotion is worth more than own-voice here. Getting *both* (own timbre + emotion) remains
  the CustomVoice **fine-tune on `pasha`** path (10–30 min of emotionally-varied audio, full FT on the
  Desktop 4070/1.7B) — deferred, revisit if own-voice becomes a hard requirement.
- Prod engine flips `TTS_ENGINE=voice_clone → custom_voice` (model `…-1.7B-CustomVoice`). The clone /
  VoiceDesign engines stay in the registry.
- Watch-outs from the `ryan` phrase test: long sentences can over-stretch (~19 s "calm") and
  number+latin content (`8080`, `Python`, `FastAPI`) ran long — verify no runaway; may need
  `max_new_tokens` clamping, chunking, or text normalization.
- New surface area: an HTTP control endpoint + persisted voice state in the agent worker, and a
  frontend voice-selector — tracked by #48.

## Alternatives considered

- **Stay on the `pasha` clone (stable, no emotion)** — shippable but flat; rejected as the target is an
  *alive* voice.
- **VoxCPM2 (ADR-0018) / CosyVoice2 (ADR-0019)** — expressive-Russian attempts; too slow / bad Russian
  respectively.
- **CustomVoice fine-tune on `pasha` now** — the only way to keep own timbre *and* emotion, but needs
  data collection + a full fine-tune; deferred behind shipping the preset-speaker path.
- **VoiceDesign persona instead of a preset speaker** — also instruct-native and reliable, but the seed
  samples the *timbre* (less stable identity) and it is not one of a fixed switchable set.
