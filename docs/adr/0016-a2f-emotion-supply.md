---
tags:
  - voice-agent
  - adr
status: accepted
relates-to: "0012-audio2face-hybrid-facial-animation, 0013-a2f-driven-lipsync-volume-fallback, 0015-a2f-helper-production"
---

# Emotion Supply for Facial Animation: A2E from Audio + Emotion-Tag Boost

Epic 8 shipped A2F facial animation whose emotion input is effectively disconnected: the only
source is the LLM inline emotion tag (5-value enum), and `neutral`/absent — the common case —
maps to an all-zeros A2E vector (`infra/desktop/a2f/emotion.py`). Our slim helper
(`infra/desktop/a2f/a2f_stream/main.cpp`, ADR-0015) feeds A2F's emotion accumulator only from
that vector and **does not run Audio2Emotion (A2E)** — the audio-prosody emotion inference the
full A2F NIM always blends in. Result: A2F degenerates to near-pure phoneme articulation and the
face is visually indistinguishable from volume lip-sync (see `docs/retro-epic8-a2f.md`).

## Decision

- **A2E audio inference becomes the baseline emotion source.** The helper runs the A2E network
  on the same PCM it already feeds A2F, so the emotion vector continuously follows the voice's
  prosody with no new services and no LLM involvement.
- **The LLM emotion tag becomes an additive boost, not the sole source.** The tag's sparse
  vector is combined on top of the A2E output (bias/amplify its dimension), preserving the
  existing tag pipeline (SOUL.md enum → agent parse → per-sentence `/tts` field → A2F fork).
- **While integrating, tune the helper's SDK knobs** (emotion strength / face params /
  per-blendshape multipliers), which today sit at library defaults.
- **Execution is gated by a calibration experiment**: the same utterance rendered with a forced
  `joy=1.0` vector vs zeros, compared on the raw-ARKit `?facedebug=1` overlay. If even a forced
  full-strength emotion barely moves the raw face, the investment redirects to SDK
  tuning/model configuration first — not to emotion sourcing.

## Alternatives considered

- **Richer LLM tags only** (10-dim enum + intensity, per-clause) — cheap (prompt + mapping),
  but depends entirely on LLM discipline, samples emotion at sentence granularity, and cannot
  capture what the voice actually does (TTS prosody). Kept as the *boost* half only.
- **A dedicated emotion-classifier service** (text/audio → emotion, separate Desktop service) —
  most controllable, but adds a service, VRAM, and latency to solve what A2E already does
  inside the engine we run. Revisit only if A2E quality disappoints.
- **Frontend-only fake** (bias Live2D params from the tag in the browser) — no per-frame
  dynamics, diverges from the A2F data path; rejected as the primary mechanism (a thin frontend
  accent layer still exists per the retro's Live2D amplification step, but as styling, not as
  the emotion source).

## Consequences

- The helper (C++) grows an A2E stage; helper image rebuild + a tuning/calibration pass are
  required (`deploy/build_image.sh`).
- Emotion becomes continuous and voice-driven; the tag shifts from "the emotion" to "the
  intent bias", matching its original purpose.
- The glossary entries **Expression**/**Emotion tag** in `.yoke/context.md` describe this
  split; until the helper work lands, the code remains tag-only and flat — the glossary marks
  A2E as decided-but-pending.
- Downstream, the Live2D mapping layer can rely on meaningful brow/eye/mouth-form dynamics,
  which the retro's amplification step (nonlinear gain + discrete accents) then exaggerates
  for the anime art style.
