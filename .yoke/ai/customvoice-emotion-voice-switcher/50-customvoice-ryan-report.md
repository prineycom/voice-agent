# Report — #50 Adopt CustomVoice speaker `ryan` as the production voice

**Issue:** https://github.com/prineycom/voice-agent/issues/50 · **Status:** code complete, live deploy in the epic's consolidated deploy step.

## What changed

- `infra/desktop/tts/engines.py` — `CustomVoiceEngine`:
  - Default speaker `aiden` → **`ryan`** (ADR-0020 prod voice).
  - Reads sampling knobs from env and passes them to `generate_custom_voice_streaming`
    (signature verified on the Desktop `.venv`, `faster_qwen3_tts` 0.2.6): `temperature`
    (0.8), `top_p` (0.9), `top_k` (50), `repetition_penalty` (1.05), `max_new_tokens`
    (2048 — the runaway-guard knob, tuned in #51). **No fixed seed** (ADR-0020: a global
    seed freezes one draw).
  - `health_fields` now also reports `temperature`, `top_p`, `max_new_tokens`.
- `infra/desktop/tts/.env.example` — prod block flipped to `custom_voice` / `ryan` /
  `language=Russian`, the seven switchable presets documented, sampling defaults, and a
  one-line rollback (`TTS_ENGINE=voice_clone`).

## Verification

- Compatible with the existing Desktop TTS test suite (fakes accept `**kwargs`; speakers
  set explicitly; `test_custom_voice_default_falls_back_to_speaker` already expects
  `ryan`). Suite run recorded in the consolidated deploy step (shared with #51).
- **Deploy + live `ryan` conversation + `/health` engine/speaker check**: done in the
  epic's consolidated deploy (Desktop checks out this branch, `.env` → `custom_voice`,
  restart `voice-agent-tts`). Kept out of prod until the branch is reviewed/merged.

## Rollback

`TTS_ENGINE=voice_clone` (+ its `TTS_MODEL`/`TTS_REF_*`) in the Desktop `.env`, restart —
returns to the `pasha` clone. Documented in `.env.example`.

## AC status

- [x] engine/speaker flipped (code + config) — deploy consolidated at epic end
- [x] `/health` exposes active engine/speaker
- [x] documented one-line rollback
- [~] live conversation in `ryan` (audio + A2F) — consolidated deploy / human sign-off
