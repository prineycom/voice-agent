---
tags:
  - voice-agent
  - adr
status: rejected
relates-to: "0003-websocket-stt-tts, 0016-a2f-emotion-supply, 0018-voxcpm2-expressive-tts"
---

# CosyVoice2-0.5B for Realtime Expressive Russian TTS — Rejected (bad Russian)

We still want a **smooth, realtime, expressive Russian** TTS (voice clone + emotion + inline
non-verbals) on the Desktop RTX 4070 alongside STT + Audio2Face. Prod is Qwen3-TTS `voice_clone` —
smooth but **emotionally flat** (ADR-0018's problem statement stands). VoxCPM2 was expressive and
Russian-capable but **too slow** on the 4070 with the A2F fork (RTF ≈ 1.65–2.97; rolled back,
blocked on Nano-vLLM). Issue #47 proposed **CosyVoice2-0.5B** as a lighter candidate — 0.5B (vs
VoxCPM's 2B diffusion), streaming, zero-shot clone, `<|endofprompt|>` emotion instructions, inline
`[laughter]`/`[breath]`, Apache-2.0 — far more likely to hit realtime beside A2F **if** its Russian
was good. Russian quality was the disputed, decisive unknown: the HF model card claims 9 languages
incl. Russian; the paper (arXiv 2412.10117) + demo page show only ZH/EN/JA/KO.

## Decision

**Rejected.** A spike ([`docs/research/2026-07-11-cosyvoice2-tts-spike.md`](../research/2026-07-11-cosyvoice2-tts-spike.md))
built CosyVoice2-0.5B in an isolated WSL2 conda env and synthesized 6 zero-shot Russian clones of
the `pasha` voice. **The Russian is bad** — every clip ran **2–3.5× longer** than the sentence
should take (runaway/rambling generation), and the user's listening verdict at the decisive gate
was **RED**. This empirically resolves the disputed-Russian question against CosyVoice2-0.5B: the
paper/demo's ZH/EN/JA/KO-only coverage reflects reality, not the model card's 9-language claim.

Consequences:
- **Prod stays on Qwen3-TTS `voice_clone`.** It was never touched during the spike (synthesis ran in
  a separate WSL2 conda env, not the prod TTS service). No `CosyVoiceEngine` was added to the
  registry; the WS `/tts` protocol, A2F fork, and agent side are unchanged.
- **VoxCPM2 remains the documented expressive fallback** (ADR-0018), still blocked on Nano-vLLM
  acceleration to reach realtime-with-A2F.
- Emotion/non-verbal quality and the RTF-with-A2F latency gate were **not evaluated** — they were
  gated behind Russian quality, which failed first.

## Alternatives considered

- **Integrate CosyVoice2-0.5B anyway (clone-only, no expressive)** ❌ — moot: the base Russian voice
  itself is unacceptable, not just the expressive layer.
- **Tune around the runaway generation** (params, text normalization, a shorter/cleaner prompt ref,
  streaming vs non-streaming) ❌ — offered at the gate; declined. 3–3.5× overruns across every
  sentence indicate the model, not a tunable, lacks reliable Russian; not worth the spike budget.
- **CosyVoice2-0.5B for a non-Russian deployment** — out of scope; Russian is a hard requirement here.
- **Stay on Qwen (status quo)** ✅ — the outcome. Flat but smooth and realtime with A2F.
- **Resume VoxCPM2 via Nano-vLLM** — the live path back to expressive Russian (ADR-0018), unaffected
  by this rejection.

## Consequences

- No code changes landed in this repo from #47; the only artifacts are this ADR and the spike log.
- The CosyVoice2 spike env (WSL2 conda env `/root/miniconda3/envs/cosyvoice`, repo `/root/CosyVoice`,
  model `/mnt/e/AI/models/CosyVoice2-0.5B`, `/mnt/e/cosyvoice-spike/`) is left in place for a possible
  future re-attempt; ~several GB, safe to prune (185 GB free on WSL `/`).
- The search for expressive Russian TTS on a 12 GB 4070 remains open: Qwen (flat, shipping) →
  VoxCPM2 (expressive, too slow, needs Nano-vLLM) → CosyVoice2-0.5B (fast enough in principle, but
  **bad Russian**, rejected).
