---
tags:
  - voice-agent
  - adr
status: accepted
relates-to: "0003-websocket-stt-tts, 0009-agent-authoritative-motion-emotion, 0014-local-llm-qwen35-mtp, 0016-a2f-emotion-supply"
---

# Expressive Voice: VoxCPM2 Emotion-Aware TTS Replacing Qwen3-TTS

The spoken voice is emotionally flat, and the avatar's face is flat as a downstream consequence.
Root cause (verified this session): the agent already sends a per-sentence `emotion` field on
every `/tts` request (`tts_plugin.py` → `server.py:157`), but **the Qwen synthesis call silently
drops it** — emotion is consumed only by the A2F face fork. Worse, the voice-clone engine we run
loads `Qwen3-TTS-12Hz-1.7B-Base`, and the Base model **cannot** apply emotion/instruction to a
cloned voice at all (only CustomVoice/VoiceDesign can; Base would require fine-tuning on the voice
dataset). So on our chosen engine, emotional voice is unreachable without a model change.

Because A2E infers the face's emotion **from the voice's prosody** (ADR-0016), a flat voice
starves the face too — fixing the voice fixes both.

## Decision

Replace Qwen3-TTS voice-clone with **VoxCPM2** (OpenBMB, 2B, tokenizer-free) as the TTS engine:
it supports style/emotion guidance **while cloning** and is the smallest OpenBMB model with
**Russian** support (VoxCPM v1/1.5 are ~Chinese+English only), which is a hard requirement.

- **Model & fit.** VoxCPM2 (~8 GB bf16) is **quantized** (int8/int4, target ~2–4 GB) to leave
  headroom. The **local Qwen3.5-4B LLM is removed permanently** (LLM served via cloud/LiteLLM),
  reversing ADR-0014's local-LLM direction; without that, VoxCPM2 + STT + A2F/A2E will not fit a
  12 GB card. Steady-state resident set: STT (~2 GB) + VoxCPM2-quantized (~2–4 GB) +
  A2F/A2E (~1.65 GB).
- **Integration.** VoxCPM2 lands as a **new engine in the existing `Engines` registry**
  (`infra/desktop/tts/engines.py`), selected via `TTS_ENGINE`; the Qwen engines stay for instant
  rollback. The WebSocket `/tts` protocol, the A2F PCM+emotion fork, and the agent side are
  unchanged (VoxCPM2 PCM is resampled to 24 kHz by the existing `_emit_pcm`). The existing
  cloned-voice reference (WAV + transcript) is reused.
- **Emotion vocabulary — expanded.** The 5-value enum
  (`neutral | happy | sad | surprised | thinking`) grows to a **curated subset of the A2E 10-dim
  emotion space** (conversational ones: joy, sadness, anger, fear, amazement, cheekiness; the
  awkward pain/outofbreath/grief dropped). Voice and face then share **one** vocabulary and the
  face-side A2E mapping needs almost no new work. Tags gain a **coarse intensity** (low/med/high).
- **Emotion routing.** The tag drives **both** the voice (mapped enum+intensity → VoxCPM2 style
  prompt; `neutral`/absent ⇒ no style guidance) **and** the face — the ADR-0016 explicit
  tag→A2F preferred-emotion boost is **kept**, with A2E enriching from the now-expressive audio.
  Emotion stays authored explicitly by the LLM, not inferred by the TTS from text.
- **Cross-sentence continuity.** Add **rolling context-carry**: VoxCPM2 receives prior-sentence
  context so prosody flows across the per-sentence flush boundaries (today each `/tts` is an
  independent generation, so "sentences sound different"). Context **resets/re-seeds on an emotion
  change** so a prior emotion is not dragged forward.
- **Gated on a spike.** Before wiring the full pipeline, stand up quantized VoxCPM2 on the 4070 in
  isolation and validate: Russian quality on the cloned-voice ref, audible emotion range, RTF +
  time-to-first-audio (RTF numbers are from a 4090; the 4070 is weaker), and real resident VRAM
  alongside STT + A2F. Go/no-go on those results (mirrors ADR-0016's calibration gate). Status
  stays `proposed` until the gate passes.

## Status update (2026-07-09) — spike gate PASSED

Spike ([`docs/research/2026-07-09-voxcpm2-tts-spike.md`](../research/2026-07-09-voxcpm2-tts-spike.md))
validated on the 4070; user approved RU quality, clone, and emotion. Corrections to the plan:

- **VRAM: quantization NOT needed.** fp16 (`load_denoiser=False`) is ~5.7 GB; full steady state
  STT + VoxCPM2-fp16 + A2F/A2E (no Qwen, no local LLM) = 8.66 / 12 GB, ~3.3 GB free. Quantization
  demoted to optional-headroom.
- **Emotion mechanism corrected.** `voxcpm 2.0.3` has no emotion parameter; style = an inline
  `(…)` natural-language prefix, and it is interpreted (not spoken) **only in Controllable Cloning
  mode** = `reference_wav_path` alone. Ultimate Cloning (`prompt_wav_path`+`prompt_text`) speaks the
  text literally. **So the expressive path uses `reference_wav_path` (no transcript)** — user
  confirmed clone timbre still holds in this mode. This supersedes the "rolling context-carry via
  prompt_wav/prompt_text" idea (that is Ultimate mode) — cross-sentence continuity needs a
  different approach or is deferred.
- **Latency:** warm streaming first-chunk ≈ 0.42 s (great); full-utterance RTF ≈ 1.8 at
  `inference_timesteps=10`. Per-sentence flush + fast first-chunk makes short sentences fine at t10;
  timesteps (t6≈1.21, t4≈0.95) is the lever for long ones. **Requires a warmup generation at
  service start** (cold start ≈ 13 s).
- **Non-verbals:** no documented tokens; achievable (if at all) only via free-form prefix
  (`(laughing)`, `(with a heavy sigh)`).

## Alternatives considered

- **Wire the existing `emotion` field into the Qwen call / switch to VoiceDesign** ❌ — cheapest,
  but Base (our voice-clone model) can't take emotion, and VoiceDesign abandons the cloned voice.
- **Fine-tune Qwen3-TTS Base on the voice dataset** ❌ — keeps the model but is a data + training
  project for one capability VoxCPM2 has out of the box.
- **VoxCPM v1/1.5 (0.5B, ~2 GB)** ❌ — fits easily but lacks reliable Russian; the language
  requirement forces the 2B.
- **Keep the local LLM, quantize VoxCPM2 hard to coexist** ❌ — the local LLM is deemed
  unnecessary; removing it is cleaner than squeezing both onto 12 GB. (Quantization is still done,
  for headroom.)
- **Let VoxCPM2 infer emotion from text; drop the tag for voice** ❌ — least wiring, but the
  voice's emotion leaves explicit LLM/authoring control.
- **Drop the tag→A2F boost, let A2E carry the face from the expressive voice** — cleaner single
  entry point; deferred, revisit after the expressive voice ships and the face is re-observed.

## Consequences

- Reverses ADR-0014: no local LLM on the Desktop GPU; the agent depends on cloud/LiteLLM for the
  LLM. The `voice-agent-llm` NSSM artifacts are retired.
- ADR-0016's premise (tag→A2F boost as a flat-voice workaround) weakens; the boost is retained for
  now but is a candidate for later removal once the expressive voice is validated.
- Blast radius of the vocabulary change: SOUL.md tag instructions, the `EMOTIONS` enum
  (`motion_events.py`), the enum→A2E vector map (`emotion.py`), a new enum→voice-prompt map, the
  tag parser (`EmotionTagStripper`, now also parsing intensity), and the Live2D expression map.
- One-off cleanup surfaced this session: an **orphaned `friendly_greider` container** (a duplicate
  `a2f_stream` helper from a Jul-4 spike, ~1.25–1.65 GB) and an exited `a2e-build` container should
  be pruned; they are not part of the running system.
- `TTS_ENGINE` rollback to a Qwen engine remains available while VoxCPM2 stabilizes.
