# Migrate agent LLM from cloud → local Qwen3.5-4B-MTP on the Desktop GPU — implementation plan

**Task:** Local LLM inference (no GitHub issue yet — created from a `/grill` session)
**Complexity:** medium
**Mode:** sub-agents
**Parallel:** partial (discovery → serve → route → validate)

## Goal

Replace the cloud LLM (currently `gemini-3-flash-preview` via the operator's LiteLLM proxy on the Pi) with a **local `Qwen3.5-4B-Q4_K_M` (MTP)** served by `llama.cpp` (`llama-server`) on the **Desktop RTX 4070**, reached from the Pi agent through the existing LiteLLM proxy. Must fit the VRAM budget with STT+TTS+A2F co-resident, run non-thinking for low voice latency, and use MTP self-speculative decoding for speed.

## Fixed decisions (from grill)

- **Host:** Desktop RTX 4070 (12 GB VRAM). Native Windows, NSSM-supervised, like STT/TTS.
- **VRAM budget:** plan for full co-tenancy STT (~2.5 GB) + TTS (~4 GB) + A2F batch-1 (~0.4 GB) + LLM. LLM budget ≈ **4 GB**.
- **Transport:** agent → LiteLLM (`http://127.0.0.1:4000/v1`, unchanged) → **Desktop `llama-server`** OpenAI-compatible `/v1`.
- **Which LiteLLM:** the **operator's running proxy** (port 4000) gets a new route. Agent changes only `LLM_MODEL`. Repo `infra/pi/litellm/config.yaml` updated as documentation-of-record only.
- **Fallback:** none — full replacement. (STT/TTS also live on the Desktop, so Desktop-offline already kills the pipeline; a separate LLM fallback adds no resilience.)
- **Thinking:** disabled (non-thinking) — determine the exact llama.cpp mechanism (`--reasoning-budget 0` and/or chat-template kwarg / `/no_think`) and verify no `<think>` leaks to TTS.
- **llama.cpp build:** reuse the existing **MTP-capable** build on drive `E:`. Update only if the `--spec-type draft-mtp` flag name (renamed from `mtp` on 2026-05-13) isn't present.
- **KV cache / context:** `--cache-type-k q8_0 --cache-type-v q8_0`, start `-c 8192`, measure VRAM peak, push toward 16k only if headroom allows.
- **Model file:** download `Qwen3.5-4B-Q4_K_M.gguf` from `unsloth/Qwen3.5-4B-MTP-GGUF` (MTP heads built into every quant — no separate draft file). Set `HF_HUB_DISABLE_XET=1`.
- **Supervision:** new NSSM service `voice-agent-llm`, port **8003**, boot-start LocalSystem; **not** wired into `deploy.sh` (manual restart — the GGUF is static across git pulls).
- **MTP:** `--spec-type draft-mtp --spec-draft-n-max 6 -fa on -np 1 -ngl 99` (MTP does not support `-np > 1`).

## Reference llama-server invocation (starting point — tune in Task 5)

```bat
llama-server ^
  -m E:\<models>\Qwen3.5-4B-Q4_K_M.gguf ^
  -ngl 99 -c 8192 -fa on -np 1 ^
  --spec-type draft-mtp --spec-draft-n-max 6 ^
  --cache-type-k q8_0 --cache-type-v q8_0 ^
  --reasoning-budget 0 ^
  --host 0.0.0.0 --port 8003 ^
  --api-key <LLM_LOCAL_API_KEY>
```

## VRAM math (why ~4 GB budget → 8k context is the safe starting point)

- Weights `Q4_K_M`: **~2.83 GB**.
- KV cache `q8_0` @ 8192 ctx: **~0.3–0.5 GB** (roughly half of fp16).
- Flash-attention + compute + MTP draft buffers: **~0.4–0.6 GB**.
- Total ≈ **3.5–3.9 GB** @ 8k → fits the ~4 GB budget with STT+TTS+A2F resident (~7 GB) inside 12 GB.
- @ 16k the KV roughly doubles (+~0.4 GB), pushing the peak to the edge of the budget → **measure before adopting 16k**. This is why context is empirical, not fixed.

## Tasks

### Task 1: Discovery on the Desktop (paths, build, ports) — NO changes

- **Depends on:** none
- **Scope:** M
- **What:** Establish ground truth on the Desktop over SSH (`Pavel@100.75.88.35`, MCP SSH / Tailscale).
- **How:**
  - Locate the existing llama.cpp on `E:` (`llama-server.exe`, `llama-cli.exe`); record the exact path.
  - `llama-server --help` → confirm the flags exist: `--spec-type` (value `draft-mtp`), `--spec-draft-n-max`, `--cache-type-k/-v`, `--reasoning-budget`, `-fa`, `-ngl`. Record the build commit/date. If `--spec-type` still says `mtp` (pre-2026-05-13) or `draft-mtp` is missing → flag update needed (Task 2).
  - Confirm CUDA is enabled in the build (`llama-server --version` / startup log shows CUDA devices).
  - Choose the models directory on `E:` (reuse the existing HF/model cache layout used by STT/TTS if present).
  - Confirm port **8003** is free; confirm the Pi can reach Desktop ports (STT 8001 / TTS 8002 already work) and whether a Windows Firewall rule is needed for 8003.
  - Read the operator LiteLLM config location/format (the running proxy on the Pi, port 4000) — where to add a route, current auth (`LITELLM_MASTER_KEY`), and whether it's Docker or venv/systemd.
- **Context:** memory `desktop-ssh-long-running-procs`, `voice-agent-deployment`; `infra/desktop/README.md`; `deploy.sh`.
- **Verify:** a written discovery note capturing every path/port/flag/decision above. Downstream tasks consume it.

### Task 2: (Conditional) update the llama.cpp build for MTP

- **Depends on:** Task 1
- **Scope:** S (skip) / M (rebuild)
- **What:** Only if Task 1 shows `--spec-type draft-mtp` is absent/renamed.
- **How:** `git pull` the E: llama.cpp checkout; rebuild `llama-server` (+`llama-cli`) with `-DGGML_CUDA=ON` per the existing build recipe. Keep the old binary as a fallback copy. Use the memory note on long-running Desktop procs (WMI/detached) so the build survives the 30s SSH cap.
- **Context:** memory `desktop-ssh-long-running-procs`.
- **Verify:** `llama-server --help | findstr draft-mtp` present; CUDA still enabled.

### Task 3: Download the GGUF to the Desktop

- **Depends on:** Task 1
- **Scope:** S
- **What:** Fetch `Qwen3.5-4B-Q4_K_M.gguf` from `unsloth/Qwen3.5-4B-MTP-GGUF` into the chosen `E:` models dir.
- **How:** `huggingface-cli download unsloth/Qwen3.5-4B-MTP-GGUF Qwen3.5-4B-Q4_K_M.gguf --local-dir <E:\models\...>` with `HF_HUB_DISABLE_XET=1` set (Xet hangs at 0 bytes on this Desktop). Long download → detach per the SSH long-proc pattern. Verify SHA/size (~2.83 GB).
- **Context:** memory `desktop-hf-xet-unreachable`, `desktop-ssh-long-running-procs`.
- **Verify:** file present, ~2.83 GB, `llama-cli -m <file> -p "hi" -n 8 -ngl 99` produces coherent output.

### Task 4: Bring up `llama-server` manually + tune non-thinking

- **Depends on:** Task 2, Task 3
- **Scope:** M
- **What:** Launch `llama-server` with the reference invocation; confirm OpenAI `/v1` works and thinking is off.
- **How:**
  - Start with `-c 8192`, q8_0 KV, MTP flags, `--host 0.0.0.0 --port 8003`, `--api-key`.
  - `curl http://100.75.88.35:8003/v1/chat/completions` (and from the Pi) → JSON response, no `<think>` block in `content`. If thinking leaks, apply `--reasoning-budget 0` and/or the model's chat-template kwarg / `/no_think`; settle the exact mechanism.
  - Sanity-check MTP is active in the server startup log (draft/acceptance stats).
- **Context:** Task 1 discovery note.
- **Verify:** `/v1/models` and `/v1/chat/completions` respond from the Pi; a Russian prompt returns clean Russian text with zero thinking leakage.

### Task 5: VRAM + context tuning under full co-tenancy

- **Depends on:** Task 4
- **Scope:** M
- **What:** Find the largest safe context in the ~4 GB budget with STT+TTS(+A2F) warm.
- **How:**
  - Warm STT+TTS (and A2F helper backend if adopting it); note baseline VRAM via `nvidia-smi`.
  - Start `llama-server` at 8192, drive a long generation, record peak VRAM. Step up (12k, 16k) while peak stays under budget with a safety margin (leave ~0.5–1 GB free). Stop at the largest ctx that never approaches OOM.
  - Record the chosen `-c`, the measured peak, and the flags in the discovery note / README.
- **Context:** memory `desktop-a2f-nim-wsl` (A2F VRAM), `infra/desktop/a2f/README.md`.
- **Verify:** at the chosen context, a sustained multi-turn generation with STT+TTS(+A2F) resident shows peak VRAM under budget and no CUDA OOM in logs.

### Task 6: NSSM service `voice-agent-llm`

- **Depends on:** Task 5
- **Scope:** M
- **What:** Wrap the tuned `llama-server` command as a boot-start NSSM service (mirroring STT/TTS), plus a launch script.
- **How:** Create `infra/desktop/llm/` in the repo: a launch `.ps1`/`.bat` holding the finalized invocation (model path, `-c`, KV, MTP, port, api-key from env), an `.env.example`, and a `deploy/README.md` with the `nssm install voice-agent-llm` / `nssm set` / `nssm start` recipe (LocalSystem, `AppExit Restart`, log redirection). Health check: `GET /health` (or `/v1/models`) on 8003. Do **not** touch `deploy.sh`.
- **Context:** `infra/desktop/a2f/deploy/README.md`, `infra/desktop/tts/switch_voice.ps1`, memory `voice-agent-deployment`.
- **Verify:** service installed, `nssm start voice-agent-llm` serves 8003; survives a simulated restart (`nssm restart`); health check green.

### Task 7: LiteLLM route + agent switchover

- **Depends on:** Task 4 (endpoint live)
- **Scope:** S
- **What:** Route a LiteLLM alias to the Desktop llama-server and point the agent at it.
- **How:**
  - **Operator proxy (running, port 4000):** add a `model_list` entry — `model_name: <alias>` → `litellm_params: {model: openai/qwen3.5-4b, api_base: http://100.75.88.35:8003/v1, api_key: os.environ/LLM_LOCAL_API_KEY}`. Since this config is out-of-repo, the plan provides the exact snippet; apply via the operator's config + reload (SSH). Note the human/operator step if credentials aren't accessible.
  - **Repo documentation-of-record:** update `infra/pi/litellm/config.yaml` (and its test `tests/test_litellm_config.py`) to reflect the local route, so repo and runtime don't drift.
  - **Agent:** set `LLM_MODEL=<alias>` in `infra/pi/agent/.env` (keep `LLM_BASE_URL=http://127.0.0.1:4000/v1`, keep `LLM_REASONING_EFFORT=none`). Update `.env.example`. No `agent.py` code change expected.
- **Context:** `infra/pi/agent/.env`, `.env.example`, `infra/pi/litellm/config.yaml`, `infra/pi/agent/tests/test_litellm_config.py`, `docs/adr/0004-llm-model.md`.
- **Verify:** from the Pi, `curl http://127.0.0.1:4000/v1/chat/completions -d '{"model":"<alias>",...}'` routes to the Desktop and returns clean text; `test_litellm_config.py` green.

### Task 8: ADR + docs

- **Depends on:** Task 5, Task 6, Task 7
- **Scope:** M
- **What:** Record the architecture change and operational runbook.
- **How:** New ADR `docs/adr/00XX-local-llm-qwen35-mtp.md` (supersedes/updates 0004): decision to run local Qwen3.5-4B-MTP on the Desktop GPU, VRAM budget + measured peak + chosen context, MTP + q8_0 KV + non-thinking rationale, no-fallback rationale, transport via LiteLLM. Update `docs/DEPLOY.md` "what runs where" (add `voice-agent-llm` on the Desktop) and `CLAUDE.md` non-obvious notes. Add `infra/desktop/llm/README.md` runbook. Save a memory note for the local-LLM serving recipe.
- **Context:** `docs/adr/0004-llm-model.md`, `docs/DEPLOY.md`, `CLAUDE.md`.
- **Verify:** manual read — all facts (paths, ports, context, flags, peak VRAM) accurate and consistent.

### Task 9: Acceptance validation

- **Depends on:** all
- **Scope:** M
- **What:** Confirm the four acceptance criteria from the grill.
- **How:**
  1. **VRAM peak in budget:** `nvidia-smi` peak with STT+TTS+A2F+LLM co-resident at the chosen context — no CUDA OOM (Task 5 evidence).
  2. **Voice-to-voice latency in target:** measure first-audio latency via the agent's existing `metrics_collected` logging (`LLMMetrics.ttft` + `TTSMetrics.ttfb`); compare against the cloud baseline (nemotron ~2.2 s; north-star <1500 ms).
  3. **Live smoke test:** real multi-turn Russian conversation through the agent — sensible replies, inline emotion tags still parsed/stripped, correct language, zero `<think>` leaking into TTS/transcript.
  4. **MTP speedup:** compare tok/s with `--spec-type draft-mtp` vs. without; record acceptance rate and the observed 1.4–2.2× range.
- **Context:** `agent.py` metrics handler (from #13), ADR-0009 emotion tags.
- **Verify:** all four documented in a report; live conversation acceptance is human-gated (needs the Desktop + full pipeline up).

## Execution

- **Mode:** sub-agents
- **Order:**
  - Task 1 (discovery) — first, blocks the rest.
  - ─── barrier ───
  - Task 2, Task 3 (parallel: build update if needed ∥ model download)
  - ─── barrier ───
  - Task 4 → Task 5 (serve, then tune — sequential, both need measurement)
  - ─── barrier ───
  - Task 6, Task 7 (parallel: NSSM service ∥ LiteLLM route + agent switch)
  - ─── barrier ───
  - Task 8, Task 9 (parallel: docs ∥ acceptance)

## Verification (acceptance criteria)

1. VRAM peak under budget with STT+TTS+A2F+LLM co-resident, no OOM — Task 5, Task 9.
2. Voice-to-voice latency measured against target — Task 9.
3. Live multi-turn Russian conversation, emotion tags intact, no thinking leakage — Task 4, Task 9.
4. MTP self-speculative decoding delivers measurable speedup — Task 4, Task 9.

## Materials

- ADR-0004 — `docs/adr/0004-llm-model.md` (model-swap "one line", latency north-star; this task supersedes the cloud choice).
- Agent LLM wiring — `infra/pi/agent/agent.py:302-310`, `config.py:121-124`, `.env`.
- LiteLLM — `infra/pi/litellm/config.yaml`, `tests/test_litellm_config.py`.
- Desktop services pattern — `infra/desktop/{stt,tts,a2f}/`, `deploy.sh`.
- Model — `unsloth/Qwen3.5-4B-MTP-GGUF` (MTP built into every quant; `--spec-type draft-mtp` merged 2026-05-16).
- Memory — `desktop-ssh-long-running-procs`, `desktop-hf-xet-unreachable`, `desktop-a2f-nim-wsl`, `voice-agent-deployment`.

## Human prerequisites (not build blockers)

- **Desktop reachable** over Tailscale and powered on (`wake-desktop`) for serve/tune/acceptance.
- **Operator LiteLLM access:** the running proxy config (port 4000) is out-of-repo; adding the route may require the operator to edit it and reload. `LLM_LOCAL_API_KEY` must be set both on `llama-server` (`--api-key`) and in the LiteLLM route env.
