# Desktop local LLM service (`voice-agent-llm`)

Local LLM inference for the voice agent: **`llama.cpp` (`llama-server`)** serving
**`Qwen3.5-4B-Q4_K_M` (MTP)** on the Desktop RTX 4070, OpenAI-compatible `/v1` on
**port 8004**. The Pi agent reaches it through the LiteLLM proxy (alias
`voice-agent` → `http://192.168.1.5:8004/v1`). Replaces the cloud LLM — see
[ADR 0014](../../../docs/adr/0014-local-llm-qwen35-mtp.md).

## Why a dedicated server (not the tray on :8080)

The Desktop already runs an interactive `llama.cpp` tray launcher
(`E:\AI\llama.cpp\tray`, port 8080) that switches between large models one at a
time. The agent needs a **fixed, always-on** model, so it gets its **own**
`llama-server` on **:8004**, independent of what the tray is doing.

Both draw on the same 12 GB card. The agent server is sized to co-reside with
STT (~2.5 GB) + TTS (~4 GB) + A2F batch-1 (~0.4 GB); running a large tray model
at the same time will exhaust VRAM — that contention is operator-managed.

## Configuration

Launch flags live in [`start_llm.ps1`](start_llm.ps1); knobs are documented in
[`.env.example`](.env.example). Key choices:

| Flag | Value | Why |
|------|-------|-----|
| `-c` | `16384` | context window; KV is q8_0 GQA on a 4B → cheap (~0.27 GB) |
| `-ctk/-ctv` | `q8_0` | KV cache quant — halves KV memory, negligible quality loss |
| `-b / -ub` | `256 / 128` | micro-batch is the real VRAM lever (see below) |
| `-fa` | `on` | flash attention |
| `--spec-type` | `draft-mtp` | MTP self-speculative decoding, ~1.4–2.2× faster generation |
| `--spec-draft-n-max` | `6` | MTP draft tokens per step |
| `-np` | `1` | MTP does not support `-np > 1` |
| `-ngl` | `99` | all layers on GPU |
| `LLAMA_CHAT_TEMPLATE_KWARGS` env | `{"enable_thinking":false}` | non-thinking — no `<think>` tokens (Qwen3.5 ignores `--reasoning-budget`) |

The model file (`unsloth/Qwen3.5-4B-MTP-GGUF`) carries the MTP heads in the
quant itself — no separate draft model.

## VRAM budget & tuning (measured on the RTX 4070)

The surprise: with `q8_0` KV and GQA on a 4B, **the KV cache is tiny** (~272 MiB
@ 16k) — context is cheap. VRAM is dominated by weights and, second, by the
**compute buffers**, of which there are **two** (the main context + the MTP draft
context). Each compute buffer scales with the **micro-batch** (`-ub`), so `-ub` is
the real lever, not `-c`.

Measured footprint (STT+TTS resident at ~7.6 GB baseline, `-c 16384`):

| `-ub` | compute buf ×2 | LLM total | free (of 12 GB) |
|-------|----------------|-----------|-----------------|
| 512 (default) | 490 MiB ea | ~4.15 GB | 268 MiB |
| 256 | 245 MiB ea | ~3.76 GB | 664 MiB |
| **128 (chosen)** | **123 MiB ea** | **~3.5 GB** | **908 MiB** |

At `-ub 128` there is ~0.9 GB free with STT+TTS, and ~0.5 GB with the A2F helper
batch-1 engine (~0.4 GB) also resident — no OOM. Weights are `~2.69 GB` on-GPU
(+0.5 GB CPU-mapped). Compute buffers are reserved at load, so loaded-idle VRAM
**is** the peak. Measured throughput: **~90 tok/s (RU) / ~178 tok/s (EN)**, MTP
draft acceptance **~94 %**.

Prefill of short voice prompts is unaffected by the small `-ub`. Raise `-ub` only
if you drop the A2F helper and want faster long-prompt prefill.

## Run manually (for tuning / debugging)

```powershell
powershell -ExecutionPolicy Bypass -File E:\voice-agent\desktop\llm\start_llm.ps1
# health / smoke:
curl http://localhost:8004/health
curl http://localhost:8004/v1/models
```

## Always-on service

See [`deploy/README.md`](deploy/README.md) for the NSSM install. The service is
**not** wired into `deploy.sh` — the GGUF is static across git pulls, so restart
it manually (`nssm restart voice-agent-llm`) only when flags or the model change.
