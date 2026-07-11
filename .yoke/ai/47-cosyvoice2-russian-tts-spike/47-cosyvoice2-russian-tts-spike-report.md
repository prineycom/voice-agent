# Report: 47-cosyvoice2-russian-tts-spike

**Plan:** `.yoke/ai/47-cosyvoice2-russian-tts-spike/47-cosyvoice2-russian-tts-spike-plan.md`
**Mode:** sub-agents (human-gated spike; not fully AFK by design)
**Status:** ✅ complete — spike concluded **NO-GO** at the decisive gate (this is a definitive answer, not a failed run)

## What this was

A human-gated GPU spike (issue #47) to test **CosyVoice2-0.5B** as a realtime expressive **Russian**
TTS on the Desktop RTX 4070. The user chose "execute up to gate T4" (the decisive human listening
verdict on Russian quality). Prod TTS stayed on Qwen throughout.

## Tasks

| #   | Task                                             | Status       | Notes |
| --- | ------------------------------------------------ | ------------ | ----- |
| T1  | Install CosyVoice2 in WSL2 (conda + pynini + repo) | ✅ DONE      | torch 2.3.1+cu121, CUDA true on 4070, `CosyVoice2` imports |
| T2  | Download CosyVoice2-0.5B weights                 | ✅ DONE      | 19 files, ~3.8 GB → `/mnt/e/AI/models/CosyVoice2-0.5B` |
| T3  | Synthesize Russian pasha clones, deliver to Pi   | ✅ DONE      | 6 WAVs → `/home/priney/cosyvoice-samples/` |
| T4  | **HUMAN GATE — Russian naturalness + timbre**    | ❌ **RED**   | User verdict: Russian is bad (2–3.5× runaway durations) |
| T5–T6 | Emotion + non-verbal samples/verdict           | ⏭️ SKIPPED   | Gated behind T4 (decisive) |
| T9  | RTF-with-A2F latency gate                        | ⏭️ SKIPPED   | Gated behind T4 |
| T10–T14 | CosyVoiceEngine integration + deploy + E2E    | ⏭️ SKIPPED   | Conditional on T4 ∧ T6 ∧ T9 green |
| T15 | Spike log (always)                               | ✅ DONE      | `docs/research/2026-07-11-cosyvoice2-tts-spike.md` |
| T16 | ADR-0019 (no-go)                                 | ✅ DONE      | `docs/adr/0019-cosyvoice2-russian-expressive-tts.md` (status: rejected) |
| T17 | Validation                                       | ✅ DONE      | Prod `/health` = Qwen `voice_clone`, `model_loaded: true`; no repo code changed |

## The decisive finding

Six zero-shot Russian clones of `pasha` all ran **2–3.5× longer** than the sentence should take —
the runaway/rambling signature of zero-shot TTS on a weakly-supported language:

| File | Natural ≈ | Actual | Overrun |
|---|---|---|---|
| ru_clone_00 | ~4–5 s | 10.84 s | 2.2× |
| ru_clone_01 | ~5–6 s | 18.40 s | 3.2× |
| ru_clone_02 | ~5 s | 14.76 s | 3.0× |
| ru_clone_03 | ~5 s | 18.40 s | 3.5× |
| ru_clone_04 | ~6 s | 15.40 s | 2.5× |
| ru_clone_05 | ~5 s | 13.44 s | 2.7× |

**User verdict: RED.** Empirically resolves the disputed-Russian risk: CosyVoice2-0.5B's real
coverage matches the paper/demo (ZH/EN/JA/KO), not the model card's 9-language claim. Secondary:
offline non-streaming warm RTF ≈ 1.0 (already weak for the later A2F gate).

## Outcome

- **Prod unchanged:** Qwen3-TTS `voice_clone`, healthy (`:8002/health` ok), never touched — the spike
  ran in a separate WSL2 conda env.
- **No code landed** in the repo; only the ADR + spike log.
- **VoxCPM2 remains the documented expressive fallback** (ADR-0018), blocked on Nano-vLLM.
- **Environment de-risking done right:** the RTF-with-A2F lesson from the VoxCPM spike was baked into
  the plan (T9), but T4 failed first, so it was never needed.

## Validation

- Prod TTS `GET :8002/health` → `engine: voice_clone`, `model_loaded: true` ✅ (Qwen intact)
- `git status` clean; no changes to `infra/desktop/tts/*` ✅
- No pytest run needed — no product code changed.

## Changes summary

| File | Action | Description |
| --- | --- | --- |
| `docs/research/2026-07-11-cosyvoice2-tts-spike.md` | created | Spike log with the no-go verdict + findings |
| `docs/adr/0019-cosyvoice2-russian-expressive-tts.md` | created | ADR-0019 (status: rejected) — stay on Qwen |
| `.yoke/ai/47-cosyvoice2-russian-tts-spike/*` | created | Plan + this report |

## Commits

- `c7686a7` #47 docs(47-cosyvoice2-russian-tts-spike): add implementation plan
- `a45070c` #47 docs(47-cosyvoice2-russian-tts-spike): record CosyVoice2-0.5B Russian spike NO-GO (ADR-0019 + spike log)

## Suggested next steps

- Comment the no-go on issue #47 and close it (not done automatically — outward-facing).
- If disk matters, prune the WSL2 spike env (`/root/miniconda3/envs/cosyvoice`, `/root/CosyVoice`,
  `/mnt/e/AI/models/CosyVoice2-0.5B`, `/mnt/e/cosyvoice-spike/`).
- For expressive Russian, the live path remains **VoxCPM2 + Nano-vLLM** (ADR-0018).
