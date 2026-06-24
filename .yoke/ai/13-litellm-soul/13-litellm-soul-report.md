# Report: 13-litellm-soul

**Plan:** `.yoke/ai/13-litellm-soul/13-litellm-soul-plan.md`
**Mode:** sub-agents
**Status:** ✅ complete (build + offline tests; live multi-turn run is the human-gated acceptance — needs the Ollama Cloud key)

## Tasks

| #   | Task                                   | Status  | Commit    | Concerns |
| --- | -------------------------------------- | ------- | --------- | -------- |
| 1   | Dependencies (livekit-plugins-openai)  | ✅ DONE | `4c8fcd0` | + `915dde4` websockets pin |
| 2   | LiteLLM config.yaml + Ollama routing   | ✅ DONE | `d7f3b78` | — |
| 3   | systemd units (litellm + worker order) | ✅ DONE | `144f121` | — |
| 4   | config.py LLM/SOUL fields + .env.example | ✅ DONE | `a439e38` | — |
| 5   | SOUL.md (Russian personality)          | ✅ DONE | `2c0b4c4` | — |
| 6   | agent.py rewrite (echo→LLM, SOUL, latency) | ✅ DONE | `f9dcc31` | Minor (see below) |
| 7   | Tests (config, SOUL, RU tokenizer, litellm cfg) | ✅ DONE | `17af2e2` | — |
| 8   | Docs (README)                          | ✅ DONE | `e3699d6` | — |
| 9   | Validation                             | ✅ pass | —         | live run human-gated |

All substantive tasks passed combined spec+quality review (T1 trivial dep — waived; T8 docs — waived). Verdicts: T2 ✅, T3 ✅, T4 ✅, T5 ✅, T6 ✅, T7 ✅.

## Post-implementation

| Step          | Status   | Commit |
| ------------- | -------- | ------ |
| Validate      | ✅ pass (28 tests) | — |
| Documentation | ⏭️ folded into T8 (`--update-docs` not set) | — |
| Format        | ✅ no formatter configured (matches #11/#12) | — |

## Concerns

### Task 6: agent.py rewrite

Reviewer noted one Minor (cosmetic): the inherited `turn_detection="vad"` kwarg is deprecated in 1.6.2 (forward path `turn_handling=TurnHandlingOptions(...)`) and emits a logger warning at runtime; the code comments call out the Silero and `metrics_collected` deprecations but not this one. Non-blocking — recorded for a future cleanup.

## Validation

- Lint: skip (no linter configured — consistent with #11/#12)
- Type-check: skip (none configured)
- Test: ✅ `.venv/bin/python -m pytest tests/ -q` → **28 passed** (19 from #11/#12 + 9 new: config defaults/overrides/missing-key regression, SOUL load/missing/empty, RU sentence tokenizer + abbreviation, litellm config validity)
- Build: skip (N/A)
- Smoke import: ✅ `import agent, config, health, stt_plugin, tts_plugin; from livekit.plugins import openai` clean
- Dependency resolution: ✅ `livekit-plugins-openai==1.6.2` installed on aarch64/py3.13 (downgraded websockets 16.0→15.0.1 per its `<16` constraint — pin updated, suite still green)

## Changes summary

| File | Action | Description |
| ---- | ------ | ----------- |
| infra/pi/litellm/config.yaml | created | LiteLLM `model_list`: `voice-agent` → `openai/nemotron-3-super:cloud` @ `https://ollama.com/v1`, keys via `os.environ/*` |
| infra/pi/agent/deploy/litellm.service | created | systemd unit (own venv, out-of-repo EnvironmentFile, journald) |
| infra/pi/agent/deploy/voice-agent-worker.service | modified | `After=/Wants=litellm.service` ordering |
| infra/pi/agent/SOUL.md | created | Russian personality (adapted from `~/.hermes/SOUL.md`, voice-tuned, ops-rules dropped) |
| infra/pi/agent/agent.py | modified | echo removed; `openai.LLM` + SOUL instructions + `metrics_collected` first-audio latency |
| infra/pi/agent/config.py | modified | `llm_base_url`, `llm_model`, `llm_api_key`, `soul_path` |
| infra/pi/agent/requirements.txt | modified | + `livekit-plugins-openai==1.6.2`; websockets pinned `==15.0.1` |
| infra/pi/agent/.env.example | modified | LLM env block (+ OLLAMA_API_KEY note) |
| infra/pi/agent/tests/test_*.py | created ×4 | config / SOUL / RU tokenizer / litellm config |
| infra/pi/agent/README.md | modified | LiteLLM setup, Ollama key, SOUL, model-swap, latency, multi-turn smoke |

## Commits

- `0caa64d` #13 docs: add implementation plan
- `4c8fcd0` #13 build: add livekit-plugins-openai for LiteLLM proxy
- `d7f3b78` #13 feat: add LiteLLM proxy config routing voice-agent to Ollama Cloud
- `144f121` #13 feat: add litellm systemd unit and order worker after it
- `a439e38` #13 feat: add LLM and SOUL config fields
- `2c0b4c4` #13 feat: add Russian SOUL personality system prompt
- `915dde4` #13 build: pin websockets<16 for openai plugin compatibility
- `f9dcc31` #13 feat: replace echo with LLM pipeline, SOUL prompt, latency logging
- `17af2e2` #13 test: add config, SOUL, RU tokenizer, litellm config tests
- `e3699d6` #13 docs: document LiteLLM proxy, Ollama key, SOUL, latency

## Key design decisions realized

- **DD-1** TTS sentence-chunking is FREE — `DesktopTTS.streaming=False` → framework auto-wraps in `StreamAdapter` with the bundled blingfire tokenizer, which splits Russian correctly (verified by test, req #7). No `tts_plugin.py` change.
- **DD-2** `openai.LLM(base_url=…/v1)` → local LiteLLM (stock plugin).
- **DD-3** LiteLLM as a standalone systemd service in its own venv (not docker); worker `After=litellm.service`.
- **DD-4** Ollama Cloud via `openai/nemotron-3-super:cloud` + `api_base: https://ollama.com/v1`, key from `os.environ/OLLAMA_API_KEY`.
- **DD-5** SOUL.md loaded at session start, fail-loud if missing/empty.
- **DD-6** First-audio latency via `metrics_collected` → `LLMMetrics.ttft` + `TTSMetrics.ttfb`.

## Human-gated acceptance (not verified here)

Requirement #6 (live multi-turn Russian conversation, first-audio latency measured) needs:
1. The **Ollama Cloud API key** placed in the litellm `EnvironmentFile` as `OLLAMA_API_KEY` (operator-supplied).
2. `litellm[proxy]` installed in its own venv and the `litellm.service` running on :4000.
3. STT/TTS Desktop services + SFU up (as in #11/#12).

All build steps + offline unit tests are green without these. The live run is the next step (can be driven through the same HTTPS browser page used for #12, now producing real replies instead of an echo).
