---
tags:
  - voice-agent
  - adr
status: accepted
supersedes: 0004-llm-model
---

# Local LLM: Qwen3.5-4B-MTP on the Desktop GPU via llama.cpp

Replace the cloud LLM (nemotron-3-super via Ollama Cloud, ADR 0004 — in practice
`gemini-3-flash-preview` on the operator's LiteLLM) with a **local
`Qwen3.5-4B-Q4_K_M` (MTP)** served by **`llama.cpp` (`llama-server`)** on the
Desktop RTX 4070. The Pi agent is unchanged: it still talks to LiteLLM (alias
`voice-agent`); LiteLLM now routes to the Desktop `llama-server` instead of the
cloud. Motivation: autonomy/privacy (no cloud dependency) and cost — the whole
voice pipeline (STT, TTS) already lives on the Desktop, so a Desktop-offline
state kills the pipeline regardless; a separate cloud LLM added no resilience.

## Decisions

- **Host: Desktop RTX 4070**, native Windows, NSSM service `voice-agent-llm` on
  port **8004** (8001 STT, 8002 TTS, 8003 A2F-reserved). Dedicated + always-on,
  separate from the interactive llama.cpp tray server on :8080 so model-switching
  there never takes the agent's LLM down.
- **Transport:** agent → LiteLLM (`:4000/v1`, unchanged) → `http://192.168.1.5:8004/v1`
  (Desktop LAN; tailnet `100.75.88.35:8004` is the fallback). One-line model swap
  preserved (ADR 0004 principle).
- **No cloud fallback.** Full replacement — consistent with STT/TTS already being
  hard Desktop dependencies.
- **Model:** `unsloth/Qwen3.5-4B-MTP-GGUF`, `Q4_K_M` (2.83 GB). MTP heads are
  built into every quant — no separate draft model.
- **MTP self-speculative decoding** (`--spec-type draft-mtp --spec-draft-n-max 6`)
  for speed. Measured **~94 % draft acceptance**, ~90 tok/s (RU) / ~178 tok/s (EN).
  Requires a llama.cpp build with the MTP PR (the Desktop's 2026-05-13 CUDA build
  already has `--spec-type draft-mtp`).
- **Non-thinking** for voice latency. Qwen3.5 honors the jinja `enable_thinking`
  kwarg — **not** `--reasoning-budget` (verified: budget 0 still emits a full
  reasoning block). Set server-wide via `LLAMA_CHAT_TEMPLATE_KWARGS={"enable_thinking":false}`
  (the env var, because PowerShell 5.1 corrupts the JSON in the `--chat-template-kwargs`
  CLI flag).

## VRAM budget (measured, 12 GB card)

KV cache is tiny under `q8_0`+GQA (~0.27 GB @ 16k) — context is cheap. VRAM is
dominated by weights (~2.69 GB on-GPU) and **two** compute buffers (main + MTP
draft), which scale with the micro-batch. Tuning `-ub 128`, `-c 16384`:

- LLM footprint ≈ **3.5 GB**; **~0.9 GB free** with STT+TTS, **~0.5 GB** with the
  A2F helper batch-1 engine also resident. No OOM. Compute buffers are reserved
  at load, so loaded-idle VRAM is the peak.

## Consequences

- LLM TTFT is now local-GPU bound; generation is fast (MTP) and fully offline.
- The Desktop is a single point of failure for the whole agent (already true for
  STT/TTS).
- Running a large model on the tray server (:8080) at the same time will exhaust
  VRAM — operator-managed contention.
- Upgrade path: swap the GGUF (e.g. a larger Qwen3.5 MTP quant) and re-tune `-ub`,
  or point the LiteLLM `voice-agent` alias back at a cloud model (one line).

## Considered alternatives

- **Pi 5 CPU inference** ❌ — 4B Q4 on ARM CPU ~5–10 tok/s, misses the voice
  latency target.
- **Direct agent → llama-server** (bypass LiteLLM) ❌ — loses the one-line swap
  point and the single config surface.
- **`--reasoning-budget 0`** ❌ — ignored by the Qwen3.5 template; still thinks.
- **Larger context via fp16 KV** ❌ — unnecessary; q8_0 KV is already cheap and
  the constraint is compute buffers, not KV.
