# Report: local-llm-qwen35-mtp

**Plan:** .yoke/ai/local-llm-qwen35-mtp/local-llm-qwen35-mtp-plan.md
**Mode:** sub-agents (orchestrator-driven; live infra executed directly over SSH)
**Status:** ✅ complete (one human-gated step remains: operator LiteLLM route + live voice smoke)

## Tasks

| # | Task | Status | Commit | Concerns |
| --- | ---- | ------ | ------ | -------- |
| 1 | Desktop discovery | ✅ DONE | — | — |
| 2 | Update llama.cpp for MTP | ⏭️ SKIPPED | — | build already has `draft-mtp` |
| 3 | Download GGUF | ✅ DONE | — | 2.83 GB, curl direct (Xet bypass) |
| 4 | Bring up llama-server + non-thinking | ✅ DONE | `0b9369b` | reasoning-budget ineffective → enable_thinking=false |
| 5 | VRAM + context tuning | ✅ DONE | `0b9369b` | `-ub` is the lever, not context |
| 6 | NSSM service + repo scaffold | ✅ DONE | `0b9369b`,`738f6c2` | running, auto-start |
| 7 | LiteLLM route + agent switch | ⚠️ DONE_WITH_CONCERNS | `c4181d7`,`e982084` | operator route is user's manual step |
| 8 | ADR + docs | ✅ DONE | `2acfe61` | — |
| 9 | Acceptance validation | ⚠️ DONE_WITH_CONCERNS | — | live voice smoke human-gated |

## What is live now

- **Desktop `voice-agent-llm`** NSSM service (LocalSystem, auto-start) running
  `llama-server` on `0.0.0.0:8004` from the git checkout
  `E:\voice-agent-repo\infra\desktop\llm\start_llm.ps1`.
- Model `Qwen3.5-4B-Q4_K_M` (MTP) at `E:\AI\models\qwen3.5\`.
- Config: `-c 16384`, `q8_0` KV, `-ub 128`, `--spec-type draft-mtp --spec-draft-n-max 6`,
  `LLAMA_CHAT_TEMPLATE_KWARGS={"enable_thinking":false}`, `-np 1`, `-ngl 99`.
- Reachable Pi→Desktop over Tailscale: `http://100.75.88.35:8004/v1` (health 200,
  model `qwen3.5-4b`).
- Repo merged to `main` and pushed; Pi + Desktop checkouts pulled to latest.
- Live agent `.env` set `LLM_MODEL=qwen3.5-4b` (backup `.env.bak-pre-qwen35`).
  **Worker not yet restarted** — deferred until the operator LiteLLM route exists.

## Measured acceptance

| Criterion | Result |
| --- | --- |
| VRAM peak in budget (no OOM) | ✅ 11105/12282 MiB used, **908 MiB free** with STT+TTS; ~508 MiB with A2F helper too. Compute buffers reserved at load ⇒ this is the peak. |
| MTP speedup | ✅ draft acceptance **~94 %**; **90 tok/s (RU) / 178 tok/s (EN)** |
| Non-thinking | ✅ server-default (no per-request kwarg): content populated, `reasoning_content` empty, finish `stop` — EN + RU |
| Voice-to-voice latency | ⏳ pending live call (agent `metrics_collected` logs TTFT+TTFB) |
| Live smoke test | ⏳ pending operator route + browser session |

## Concerns

### Task 4/5: non-thinking mechanism + VRAM lever (resolved, documented)

`--reasoning-budget 0` is **ignored** by the Qwen3.5 template (it still emitted a
full reasoning block that consumed the whole token budget). The working switch is
the jinja `enable_thinking=false` kwarg, set server-wide via the
`LLAMA_CHAT_TEMPLATE_KWARGS` env var (PowerShell 5.1 corrupts the JSON in the
`--chat-template-kwargs` CLI flag). Separately, KV cache is tiny under q8_0+GQA, so
the micro-batch (`-ub`), not context, is the VRAM lever — `-ub 128` buys the A2F
headroom. Both captured in ADR 0014 and the service README.

### Task 7: operator LiteLLM route is the user's manual step

The running LLM proxy on `:4000` is operator-managed
(`/home/priney/selfhost/litellm/config.yaml`, shared with kimi/qwen3.6/etc.). Per
the user's decision, they add the `qwen3.5-4b` route themselves. Until then the
worker keeps serving the previous model; it must be restarted **after** the route
is added. Snippet to add:

```yaml
  - model_name: qwen3.5-4b
    litellm_params:
      model: openai/qwen3.5-4b
      api_base: http://100.75.88.35:8004/v1
      api_key: dummy
```

Then: reload the proxy and `sudo systemctl restart voice-agent-worker`.

## Validation

- `pytest tests/test_litellm_config.py` ✅ 1 passed
- Pi→Desktop `curl /health` ✅ 200; `/v1/models` ✅ `qwen3.5-4b`
- llama-server live chat (EN+RU) ✅ non-thinking, MTP active
- NSSM `status voice-agent-llm` ✅ SERVICE_RUNNING

## Changes summary

| File | Action | Description |
| --- | --- | --- |
| infra/desktop/llm/start_llm.ps1 | created | tuned llama-server launch |
| infra/desktop/llm/.env.example | created | documented knobs + measured values |
| infra/desktop/llm/README.md | created | service + VRAM/tuning notes |
| infra/desktop/llm/deploy/README.md | created | NSSM recipe (checkout path) |
| infra/desktop/llm/deploy/install_nssm_llm.ps1 | created | idempotent installer |
| infra/pi/litellm/config.yaml | modified | alias qwen3.5-4b → Desktop:8004 (tailnet) |
| infra/pi/agent/tests/test_litellm_config.py | modified | assert local route |
| infra/pi/agent/.env.example | modified | LLM_MODEL=qwen3.5-4b |
| docs/adr/0014-local-llm-qwen35-mtp.md | created | decision record (supersedes 0004) |
| docs/DEPLOY.md | modified | +voice-agent-llm row |
| CLAUDE.md | modified | non-obvious note |

## Commits

- `999bcb8` docs: add implementation plan
- `0b9369b` feat: Desktop llama.cpp LLM service (Qwen3.5-4B-MTP)
- `c4181d7` feat: route LiteLLM alias to local llama-server
- `2acfe61` docs: ADR 0014 + DEPLOY/CLAUDE
- `e982084` fix: Tailscale endpoint + qwen3.5-4b alias
- `21e0e93` fix: run NSSM service from git checkout path
- `738f6c2` chore: idempotent NSSM installer
- `adac436` Merge to main
