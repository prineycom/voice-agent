# VoxCPM2 Expressive-TTS Spike (go/no-go gate for ADR-0018)

**Date:** 2026-07-09 · **Status:** in progress · **Gates:** ADR-0018 (`proposed`)

## Why
Qwen3-TTS voice is emotionally flat and — since A2E infers the face emotion from the voice's
prosody — the avatar's face is flat as a consequence. Verified root cause: the `emotion` field
already reaches `/tts` but Qwen synthesis drops it, and our voice-clone engine runs
`Qwen3-TTS-12Hz-1.7B-Base`, which **cannot** apply emotion to a cloned voice at all. ADR-0018
decides to switch to **VoxCPM2** (2B, Russian, emotion-while-cloning). This spike is the go/no-go
gate before wiring the full pipeline.

## What VoxCPM2 gives us (from the API)
- `VoxCPM.from_pretrained("openbmb/VoxCPM2", load_denoiser=False)`; `pip install voxcpm`.
- Clone = `prompt_wav_path` + `prompt_text` ("Ultimate Cloning"); reuse our existing ref.
- **Emotion = inline text prefix** `(cheerful, slightly faster tone)…`, NOT a parameter.
- `generate_streaming(...)` yields chunks; context-carry via the same prompt mechanism.
- Output **48 kHz** → resample to 24 kHz (existing `_emit_pcm` handles it). A2F fork unchanged.
- ~8 GB fp16 / RTF ~0.30 on a **4090** (4070 is weaker → must measure). No native int8/int4 in
  the Python API — quantization path is GGUF via `llama.cpp-omni` or Nano-vLLM. **Quantization is
  mandatory** (fp16 won't fit) and is the biggest risk.

## Constraints
- Isolated venv, do **not** touch the running `voice-agent-stt/tts/a2f`.
- **Never** load VoxCPM2 into the GPU while prod is fully resident — only ~4.8 GB free; an 8 GB
  fp16 load would OOM and crash prod. GPU-load/VRAM-measure only happens after stopping prod TTS
  and/or on the quantized model, in a controlled step.
- Desktop gotchas: torch/torchaudio from **cu124** index (else WinError 127);
  `HF_HUB_DISABLE_XET=1` (else HF download hangs at 0 B); MCP-SSH 30 s cap → long jobs run
  **detached via WMI** with a log + DONE/FAIL markers.

## Steps
| # | Step | Metric |
|---|---|---|
| 0 | Isolated venv `E:\voxcpm-spike\.venv`, install torch cu124 + voxcpm, **download weights only** (no GPU load) | install ok, torch.cuda true, weights on disk |
| 1 | Smoke: load `from_pretrained(load_denoiser=False)`, one RU sentence — **after stopping prod TTS** | real fp16 resident VRAM (test the ~8 GB claim) |
| 2 | Clone: `prompt_wav_path`+`prompt_text` = current `TTS_REF_AUDIO/TEXT`, 5–6 RU phrases | RU quality + timbre match *(user's ears)* |
| 3 | Emotion: one phrase × curated enum (joy/sad/anger/fear/amazement/cheekiness) × intensity low/med/high via `(…)` prefix | emotions audible & sensible *(user's ears)* |
| 4 | `generate_streaming` + timers | time-to-first-chunk + RTF on 4070 vs Qwen |
| 5 | Continuity PoC: prev sentence (audio+text) as prompt for next, 3 phrases | prosody flows across boundary |
| 6 | **Quantization**: GGUF via `llama.cpp-omni` (or fp8) → re-run 1–4 | quantized VRAM + quality/latency delta vs fp16 |
| 7 | Coexistence: stop `voice-agent-tts`, load quantized VoxCPM2 beside STT+A2F/A2E, `nvidia-smi` | resident set ≤ ~10.5 GB (≥1.5 GB margin) |

## Go/no-go criteria
- **VRAM:** quantized VoxCPM2 + STT + A2F/A2E ≤ ~10.5 GB (margin ≥ 1.5 GB). ← hardest
- **Latency:** time-to-first-audio ≤ ~700 ms; sustained RTF < 1.0 (ideally ≤ 0.6). Levers:
  `inference_timesteps` 10→6–8, Nano-vLLM.
- **RU + clone:** intelligible, natural, recognizably the cloned voice. *(subjective — user)*
- **Emotion:** blind-distinguishable joy/sad/anger/neutral; low vs high intensity audible. *(user)*

**Go** = all four green → ADR-0018 → `accepted`, build the integration. **No-go** branches:
quantized quality poor → less-aggressive quant / drop something else; latency high →
timesteps/Nano-vLLM; won't fit → stop signal for VoxCPM2 on this hardware.

## Log
- 2026-07-09: doc created; step 0 launched (detached, `E:\voxcpm-spike\step0.log`).
- 2026-07-09: **step 0 DONE.** venv `E:\voxcpm-spike\.venv`; torch 2.6.0+cu124 + torchaudio,
  `torch.cuda.is_available()=True` on RTX 4070; `voxcpm 2.0.3` (transformers 5.13, funasr, gradio).
  VoxCPM2 weights ~4.6 GB downloaded to `E:\AI\models\hub\models--openbmb--VoxCPM2\snapshots\bffb3df5…`
  (HF cache is on E:; `HF_HUB_DISABLE_XET=1` avoided the Xet hang; model ungated). No GPU load, prod
  untouched. **Note for step 1:** on-disk weights ~4.6 GB ⇒ fp16 resident ≈ 5–6 GB, which will NOT
  fit the ~4.8 GB free while Qwen-TTS is resident — step 1 must briefly stop `voice-agent-tts` to
  measure safely.
- 2026-07-09: **steps 1–4 DONE** (stopped `voice-agent-tts` ~2 min, then restored; A2F/STT untouched).
  API note: installed `voxcpm 2.0.3` `generate()` does **not** accept `seed`; emotion = inline
  `(…)` prefix; output 48 kHz. Findings:
  - **VRAM (fp16, `load_denoiser=False`): ~5.7 GB resident** (torch reserved 5490 MB, load 21 s).
    Full steady state **STT + VoxCPM2-fp16 + A2F/A2E, no Qwen, no local LLM = 8656 MB used /
    3357 free**. ⇒ **fits without quantization** (~3.3 GB margin). Qwen-TTS itself was ~4.2 GB.
    **Revises ADR-0018:** quantization is now optional (headroom), not mandatory. Biggest-risk step
    downgraded.
  - **Latency (warm; cold start is ~13 s and must be discarded — do a warmup gen at service boot):**
    streaming **first_chunk = 0.42 s** (excellent for turn latency). Full-utterance **RTF ≈ 1.8 at
    `inference_timesteps=10`** on the 4070 → long single utterances underrun in streaming.
    Timesteps is the lever: **t6 → RTF 1.21, t4 → RTF 0.95** (real-time). Per-sentence flush + fast
    first-chunk means short sentences stream fine at t10; long ones need t≤6 or Nano-vLLM.
  - Samples in `E:\voxcpm-spike\samples\`: `clone_neutral`, `emo_{neutral,happy,sad,anger}` (t10,
    same sentence = emotion A/B), `emo_neutral_t6`, `emo_neutral_t4` (quality-vs-speed), `stream`.
    **Pending: user listening verdict** on clone timbre match, emotion distinctness, and the lowest
    acceptable `inference_timesteps`.
- 2026-07-09: **emotion did NOT apply in step 1 — wrong cloning mode.** The `(…)` style prefix was
  spoken aloud as text. Root cause: step 1 used **Ultimate Cloning** (`prompt_wav_path`+`prompt_text`),
  which reads the whole text literally. Installed `voxcpm 2.0.3` `_generate()` has **no** emotion/
  style parameter; style is ONLY the parenthetical prefix, and it is interpreted (not spoken) ONLY in
  **Controllable Cloning** mode = `reference_wav_path` alone (no `prompt_text`). Three modes:
  `reference_wav_path` (controllable, supports style prefix) · `prompt_wav_path`+`prompt_text`
  (ultimate/faithful continuation, prefix spoken) · pure text (voice design). **Integration
  consequence:** the expressive path must use `reference_wav_path` (controllable), i.e. we give up
  ultimate cloning's transcript-based fidelity — so clone timbre in this mode needs its own check
  (`clone_ref_plain.wav`). Step-2 re-test (mode 2) confirms indirectly: same sentence, `fix_neutral`
  (no prefix) 2.40 s vs `fix_happy` (with prefix) 1.92 s — prefix no longer adds spoken duration
  (step-1 ultimate `emo_happy` was 6.88 s because it spoke the prefix). **Non-verbals** (laughter/
  sighs/breaths) are **undocumented** — no tokens like `[laugh]`; probed via free-form prefix
  (`nv_laugh`, `nv_sigh`, `nv_whisper`) — user to judge if they emerge. Corrected samples:
  `/home/priney/voxcpm-samples/v2-correct/` (Pi). Pending user verdict.
