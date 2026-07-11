# Plan — #50 Adopt CustomVoice speaker `ryan` as the production voice

**Issue:** https://github.com/prineycom/voice-agent/issues/50 (parent #49)
**Source of truth:** `docs/adr/0020-customvoice-emotion-and-voice-switcher.md`, `.yoke/context.md`
**Mode:** sub-agents (inline execution on epic branch)

## Goal

Flip prod TTS from `voice_clone` (Base / `pasha`) to `custom_voice`, default speaker
`ryan`, `language=Russian`, `temperature 0.8 / top_p 0.9`, **no fixed seed**. No emotion
yet — prove the whole pipeline runs end-to-end on CustomVoice and sounds good neutral.
`voice_clone`/`voice_design` stay in the registry.

## Changes

1. `infra/desktop/tts/engines.py` — `CustomVoiceEngine`:
   - Default speaker `aiden` → **`ryan`**.
   - Read sampling knobs from env (mirror `VoiceCloneEngine`): `TTS_TEMPERATURE` (0.8),
     `TTS_TOP_P` (0.9), `TTS_TOP_K` (50), `TTS_REPETITION_PENALTY` (1.05),
     `TTS_MAX_NEW_TOKENS` (2048, plumbing for the #51 runaway clamp). **No seed.**
   - Pass them to `generate_custom_voice_streaming` (verified signature accepts all).
   - `health_fields`: expose speaker + sampling so `/health` reflects the active config.
2. `infra/desktop/tts/.env.example` — document the `custom_voice` prod config: `ryan`,
   `language=Russian`, the switchable preset list, sampling defaults, and the one-line
   rollback to `voice_clone`.
3. **Deploy:** set the Desktop live `.env` (`E:\voice-agent-repo\infra\desktop\tts\.env`)
   to `TTS_ENGINE=custom_voice`, `TTS_SPEAKER=ryan`, `TTS_LANGUAGE=Russian`; restart the
   `voice-agent-tts` NSSM service; confirm `/health` shows engine `custom_voice` +
   speaker `ryan` + `model_loaded:true`.

## Verification

- `/health` returns `engine=custom_voice`, `speaker=ryan`, `model_loaded=true`.
- A WS `/tts` smoke synth of a Russian sentence returns non-empty 24k PCM without error.
- Desktop TTS unit tests (`infra/desktop/tts/tests`) pass.
- Live conversation in `ryan` (audio + A2F) — human check at epic sign-off.

## Rollback

One line in the Desktop `.env`: `TTS_ENGINE=voice_clone` (+ restart) returns to the
`pasha` clone. Documented in `.env.example`.
