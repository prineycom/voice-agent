# CosyVoice2-0.5B Expressive-TTS Spike (Russian) — go/no-go gate

**Date:** 2026-07-11 · **Status:** NO-GO (stopped at the decisive Russian gate) · **Issue:** #47 · **Relates:** ADR-0019, ADR-0018 (VoxCPM2 fallback), spike `2026-07-09-voxcpm2-tts-spike.md`

## Why
Prod TTS is Qwen3-TTS `voice_clone` — smooth (RTF 0.67 with A2F) but **emotionally flat**, which
also starves the A2E face (it infers emotion from voice prosody). ADR-0018's VoxCPM2 was expressive
but **too slow** on the 4070 with A2F (RTF ≈ 1.65–2.97, rolled back). Issue #47 proposed
**CosyVoice2-0.5B** as the next candidate: 0.5B (vs VoxCPM's 2B diffusion), streaming (~150 ms
first-packet claimed), zero-shot clone, instruction/emotion via `<|endofprompt|>`, inline
`[laughter]`/`[breath]` non-verbals, Apache-2.0. **The one open risk was Russian quality** — the HF
model card claims 9 languages incl. Russian, but the paper (arXiv 2412.10117) + demo only show
ZH/EN/JA/KO. This spike tests that first, per the plan's decisive gate T4.

## What CosyVoice2 gives us (from the API, as installed)
- Repo `FunAudioLLM/CosyVoice` (git, `--recursive`, Matcha-TTS submodule); model
  `FunAudioLLM/CosyVoice2-0.5B` (19 files, ~3.8 GB: `llm.pt` 2.0 GB Qwen2-0.5B backbone, `flow.pt`,
  `hift.pt`, `campplus.onnx`, `speech_tokenizer_v2.onnx`, `flow.decoder.estimator.fp32.onnx`).
- Zero-shot clone: `inference_zero_shot(tts_text, prompt_text, prompt_wav, stream=)`. **In this
  checkout the frontend calls `load_wav(prompt_wav, …)` internally, so `prompt_wav` must be a FILE
  PATH, not a preloaded tensor** (differs from older docs/examples).
- Emotion (untested — gate not reached): `inference_instruct2(tts_text, instruct_text, prompt_wav)`
  with an instruction terminated by `<|endofprompt|>`.
- Native **24 kHz** output (= our server contract; no resample needed, vs VoxCPM's 48 k).

## Constraints / environment
- **Ran on the Desktop WSL2 Ubuntu 22.04** (not Windows-native): CosyVoice's text-frontend stack
  (`pynini`/`wetext`/Matcha-TTS) is impractical on Windows; A2F already runs in WSL2 here, so WSL2
  was the pragmatic target (Windows-vs-WSL2 deploy decision was deferred to a later gate that we
  never reached). Isolated **conda env** `cosyvoice` (Miniconda at `/root/miniconda3`), python 3.10,
  `pynini==2.1.5` from **conda-forge** (the reliable path; pip build is the classic killer). Repo at
  `/root/CosyVoice`; model at `/mnt/e/AI/models/CosyVoice2-0.5B`; spike scripts/logs/samples under
  `/mnt/e/cosyvoice-spike/`.
- Prod (`voice-agent-stt/tts/a2f`) **left untouched** — synthesis ran in a separate process/env; the
  offline quality test did not need prod stopped.
- Desktop gotchas applied: `HF_HUB_DISABLE_XET=1`; torch/torchaudio **cu121** (per CosyVoice
  requirements pin 2.3.1); MCP-SSH 30 s cap → long jobs detached via **`systemd-run`** in WSL with
  log + DONE/FAIL markers; cmd.exe grabs `| > < & ( )` even inside quotes → all logic in uploaded
  `.sh` files, run via `wsl bash <file>`.

## Steps (planned)
| # | Step | Metric | Result |
|---|---|---|---|
| 1 | Isolated conda env + CosyVoice install (pynini, torch cu121, repo) | import ok, torch.cuda true | ✅ torch 2.3.1+cu121, CUDA true on 4070, `CosyVoice2` imports |
| 2 | Download `CosyVoice2-0.5B` to `/mnt/e/AI/models` | weights on disk | ✅ 19 files, ~3.8 GB |
| 3 | Zero-shot clone `pasha`, 5–6 RU sentences → Pi | RU quality + timbre *(ears)* | ✅ generated; ⚠️ see findings |
| **4** | **HUMAN GATE — Russian naturalness + timbre** | user verdict | ❌ **RED — bad** |
| 5–6 | Emotion + non-verbals | *(ears)* | ⏭️ not reached |
| 9 | Warm streaming RTF **with A2F concurrent** | RTF < ~0.8 | ⏭️ not reached (T4 red) |

## Go/no-go criteria
- **RU + clone:** intelligible, natural, recognizably pasha. *(subjective — user)* → **FAILED.**
- Emotion / non-verbals / RTF-with-A2F: **not evaluated** (gated behind T4).

## Findings (decisive)
Six zero-shot Russian clones of `pasha` (`/home/priney/cosyvoice-samples/ru_clone_00..05.wav`).
**Every clip ran 2–3.5× longer than the sentence should take** — the classic runaway/rambling
signature of zero-shot TTS on a weakly-supported language:

| File | Sentence (RU) | Natural ≈ | Actual | Overrun |
|---|---|---|---|---|
| 00 | Привет! Меня зовут Паша… | ~4–5 s | 10.84 s | 2.2× |
| 01 | Который сейчас час?… | ~5–6 s | 18.40 s | 3.2× |
| 02 | В двадцать первом веке технологии… | ~5 s | 14.76 s | 3.0× |
| 03 | Знаешь, я думаю, что нам стоит встретиться… | ~5 s | 18.40 s | 3.5× |
| 04 | Сегодня прекрасная погода… | ~6 s | 15.40 s | 2.5× |
| 05 | Подожди, пожалуйста, минутку… | ~5 s | 13.44 s | 2.7× |

**User listening verdict (T4): RED — Russian is bad.** This confirms the disputed-Russian risk
empirically: **CosyVoice2-0.5B does not produce acceptable Russian** on our cloned voice. The paper/
demo's ZH/EN/JA/KO-only coverage, not the model card's 9-language claim, matches reality here.

Secondary (informational, not the gate): offline **non-streaming warm RTF ≈ 1.0** on the 4070
(first two cold-ish clips ≈ 3.3), with some ONNX tokenizer nodes forced to CPU. Even had Russian
passed, RTF ~1.0 offline is a poor starting point for the T9 gate (warm RTF < ~0.8 **with** the A2F
fork, which adds ~1.6–1.7×) — CosyVoice2 was not obviously going to clear the latency bar either.

## Decision
**NO-GO.** Prod stays on **Qwen3-TTS `voice_clone`** (never touched during the spike). VoxCPM2
remains the documented expressive fallback (ADR-0018; blocked on Nano-vLLM for realtime). No
`CosyVoiceEngine` integration. See ADR-0019.

## Artifacts left in place (for a possible future re-attempt)
- WSL2 conda env `/root/miniconda3/envs/cosyvoice` (torch 2.3.1+cu121, pynini 2.1.5, cosyvoice deps;
  `deepspeed` removed — it needs a full CUDA_HOME and is training-only).
- Repo `/root/CosyVoice`; model `/mnt/e/AI/models/CosyVoice2-0.5B`; scripts + samples + logs under
  `/mnt/e/cosyvoice-spike/`.
- These consume ~4 GB (model) + a few GB (env) on WSL `/` (185 GB free) — safe to keep or prune.

## Log
- 2026-07-11: env built in WSL2 (Miniconda + conda-forge pynini; conda ToS needed `--override-channels
  -c conda-forge`; `deepspeed` uninstalled to fix a transformers import that required CUDA_HOME;
  `openai-whisper` reinstalled with `setuptools<80` build-constraint — the frontend imports it
  unconditionally). torch 2.3.1+cu121, CUDA true. Model downloaded (~3.8 GB). 6 RU clones generated
  (after fixing the prompt-as-PATH API). **T4 human verdict: RED — bad Russian (2–3.5× runaway
  durations). Spike stopped. Prod remains Qwen.**
