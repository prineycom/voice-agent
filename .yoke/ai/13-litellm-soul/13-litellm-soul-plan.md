# LiteLLM proxy + real conversation with SOUL personality — implementation plan

**Task:** GitHub issue #13 (Epic 4 — Agent Worker on Pi 5)
**Complexity:** medium
**Mode:** sub-agents
**Parallel:** true

## Design decisions

### DD-1: TTS sentence-chunking is free — no `tts_plugin.py` change

**Decision:** Rely on the framework's auto-chunking; do not modify `DesktopTTS`.
**Rationale:** `tts_plugin.py:42` sets `capabilities.streaming=False`, so `Agent.default.tts_node` (`.venv/.../livekit/agents/voice/agent.py:505-510`) auto-wraps it in `tts.StreamAdapter(sentence_tokenizer=tokenize.blingfire.SentenceTokenizer(retain_format=True))` and feeds the LLM token stream sentence-by-sentence — sentence #1 synthesizes immediately (req #5).
**Alternative:** A streaming TTS or manual chunker — rejected: duplicates framework behavior and touches a file that must not change.

### DD-2: `openai.LLM` plugin pointed at LiteLLM (not a custom LLM plugin)

**Decision:** `AgentSession(llm=openai.LLM(model=cfg.llm_model, base_url=cfg.llm_base_url, api_key=cfg.llm_api_key))`.
**Rationale:** LiteLLM exposes an OpenAI-compatible API; the stock plugin drops straight in. `base_url` carries `/v1`; `api_key` must be non-empty. Plugin confirmed absent → install is real work.
**Alternative:** A bespoke `llm_plugin.py` — rejected: unnecessary given OpenAI compatibility; more surface, no benefit.

### DD-3: LiteLLM as a standalone systemd service in its own venv, not Docker

**Decision:** New `deploy/litellm.service` (own venv, `pip install 'litellm[proxy]'`, `litellm --config … --port 4000`), mirroring `voice-agent-worker.service`. The worker unit gains `After=litellm.service`.
**Rationale:** `infra/pi/docker-compose.yml` runs only the SFU; the worker already runs from a venv via systemd. A separate venv avoids dependency conflicts with the `livekit-agents==1.6.2` pins.
**Alternative:** A LiteLLM Docker container in compose — rejected: splits the runtime model across two paradigms.

### DD-4: Ollama Cloud via `openai/` prefix + `api_base: https://ollama.com/v1`

**Decision:** LiteLLM `model_list`: `voice-agent` → `model: openai/nemotron-3-super:cloud`, `api_base: https://ollama.com/v1`, `api_key: os.environ/OLLAMA_API_KEY`.
**Rationale:** Ollama Cloud serves an OpenAI-compatible endpoint; the `openai/` provider prefix + explicit `api_base` is the documented route; `:cloud` selects the hosted model; key via env (no interactive login, req #2).
**Alternative:** `ollama_chat/` provider — rejected: targets a native local Ollama daemon, not the cloud OpenAI surface.

### DD-5: SOUL loaded from a local file resolved next to `agent.py`, fail loudly

**Decision:** New `infra/pi/agent/SOUL.md` (Russian persona); `_load_soul()` reads `cfg.soul_path`, raises a clear RuntimeError if missing/empty; passed as `Agent(instructions=soul_text)`.
**Rationale:** Mirrors the `.env` resolution (`config.py`) for predictability under systemd `WorkingDirectory`; `instructions` is the framework's system-prompt channel. A personality-less agent is a silent regression we refuse.
**Alternative:** Hardcoding the prompt in `agent.py` — rejected: couples persona edits to code and bloats the module.

### DD-6: First-audio latency via `session.on("metrics_collected")`, logging `LLMMetrics.ttft` + `TTSMetrics.ttfb`

**Decision:** Register a metrics handler before `session.start`; log `LLMMetrics.ttft` (first token) and `TTSMetrics.ttfb` (first audio byte) at INFO with a `first-audio-latency` tag (req #6).
**Rationale:** The framework already computes exactly these numbers (`metrics/base.py:26,64`); no manual timestamping and no `tts_plugin.py` change.
**Alternative:** Manual `time.monotonic()` bracketing around the first `synthesize()` — rejected: would touch the do-not-modify `tts_plugin.py` and re-derive a number the framework reports. Note: `metrics_collected` is deprecated in 1.6.2 (forward path is `ChatMessage.metrics`); acceptable for MVP latency logging, noted in a comment.

## Tasks

### Task 1: Dependencies

- **Files:** `infra/pi/agent/requirements.txt` (edit)
- **Depends on:** none
- **Scope:** S
- **What:** Add `livekit-plugins-openai==1.6.2` (locked to `livekit-agents==1.6.2`).
- **How:** Add the pinned line under the silero line, commented as an OpenAI-compatible HTTP client for the local LiteLLM proxy (no OpenAI key/traffic). Add a top note that LiteLLM is NOT in this venv — it runs in its own venv as a systemd service (`pip install 'litellm[proxy]'`).
- **Context:** `infra/pi/agent/requirements.txt`.
- **Verify:** `.venv/bin/pip install -r requirements.txt` resolves; `python -c "from livekit.plugins import openai"` imports.

### Task 2: LiteLLM proxy config + Ollama Cloud routing

- **Files:** `infra/pi/litellm/config.yaml` (create)
- **Depends on:** none
- **Scope:** S
- **What:** LiteLLM `model_list` mapping `voice-agent` → Ollama Cloud.
- **How:** `model_name: voice-agent`; `litellm_params: {model: openai/nemotron-3-super:cloud, api_base: https://ollama.com/v1, api_key: os.environ/OLLAMA_API_KEY}`. Add `general_settings.master_key: os.environ/LITELLM_MASTER_KEY` (optional — placeholder for MVP). Inline comment: this `model:` line is the one-line model-swap point (req #3).
- **Context:** `docs/adr/0004-llm-model.md`.
- **Verify:** `python -c "import yaml,sys; d=yaml.safe_load(open('infra/pi/litellm/config.yaml')); assert d['model_list'][0]['model_name']=='voice-agent'"`.

### Task 3: systemd units

- **Files:** `infra/pi/agent/deploy/litellm.service` (create), `infra/pi/agent/deploy/voice-agent-worker.service` (edit)
- **Depends on:** none
- **Scope:** M
- **What:** A `litellm.service` unit + order the worker after it.
- **How:** Mirror `voice-agent-worker.service`: `Type=simple`, `After/Wants=network-online.target`, `EnvironmentFile=` (carries `OLLAMA_API_KEY`, `LITELLM_MASTER_KEY`), `Restart=on-failure`, `RestartSec=5`, journald, `SyslogIdentifier=litellm`, `ExecStart=<litellm-venv>/bin/litellm --config /opt/voice-agent/infra/pi/litellm/config.yaml --port 4000`. Header comment block: create venv, `pip install 'litellm[proxy]'`, create EnvironmentFile with the Ollama key, install/enable. In `voice-agent-worker.service` add `After=litellm.service` + `Wants=litellm.service` to `[Unit]` (ordering only; LiteLLM failures surface as runtime LLM errors).
- **Context:** `infra/pi/agent/deploy/voice-agent-worker.service`.
- **Verify:** `systemd-analyze verify` not available offline → manual read: both `[Unit]/[Service]/[Install]` present, paths consistent.

### Task 4: config.py LLM/SOUL fields + .env.example

- **Files:** `infra/pi/agent/config.py` (edit), `infra/pi/agent/.env.example` (edit)
- **Depends on:** none
- **Scope:** S
- **What:** Add `llm_base_url`, `llm_model`, `llm_api_key`, `soul_path` to `AgentConfig` + `load_config`.
- **How:** Mirror the `os.environ.get` pattern. Defaults: `LLM_BASE_URL=http://localhost:4000/v1` (comment `/v1` is mandatory), `LLM_MODEL=voice-agent`, `LLM_API_KEY=litellm-local` (non-empty placeholder; comment it must match the LiteLLM master key if one is set), `SOUL_PATH=<dir of agent.py>/SOUL.md`. In `.env.example` add an `# LLM (via local LiteLLM proxy)` block with all four, plus an explicit comment that `OLLAMA_API_KEY` lives in the litellm.service EnvironmentFile, NOT here.
- **Context:** `infra/pi/agent/config.py`, `infra/pi/agent/.env.example`.
- **Verify:** `LIVEKIT_API_KEY=x LIVEKIT_API_SECRET=y .venv/bin/python -c "from config import load_config; c=load_config(); print(c.llm_base_url, c.llm_model, c.soul_path)"`.

### Task 5: SOUL.md (Russian personality)

- **Files:** `infra/pi/agent/SOUL.md` (create)
- **Depends on:** none
- **Scope:** S
- **What:** Russian system prompt defining persona, tone, constraints.
- **How:** Spoken-friendly Russian; respond in Russian, concise sentences (reads well through sentence-chunked TTS), warm helpful persona. Persona specifics per the confirmation-gate answer.
- **Context:** —
- **Verify:** File exists, non-empty, valid UTF-8 Russian.

### Task 6: agent.py rewrite (echo → LLM + SOUL + latency logging)

- **Files:** `infra/pi/agent/agent.py` (edit)
- **Depends on:** Task 1, Task 4, Task 5
- **Scope:** L
- **What:** Replace the echo with the STT→LLM→TTS pipeline using SOUL instructions, and log first-audio latency.
- **How:** Remove `EchoAgent` + `AGENT_INSTRUCTIONS` placeholder. Add `from livekit.plugins import openai`. Add `_load_soul(path) -> str` (reads `cfg.soul_path`, raises a clear RuntimeError if missing/empty). Add `llm=openai.LLM(model=cfg.llm_model, base_url=cfg.llm_base_url, api_key=cfg.llm_api_key)` to `AgentSession(...)`; keep `stt`, `tts`, `vad=vad`, `turn_detection="vad"`, and the greeting. Use `agent=Agent(instructions=soul_text)`. Register `session.on("metrics_collected", _on_metrics)` before `session.start` to log `LLMMetrics.ttft` + `TTSMetrics.ttfb` (`first-audio-latency` tag). Update the module docstring to describe the STT→LLM→TTS pipeline.
- **Context:** `infra/pi/agent/agent.py`; `.venv/.../livekit/agents/voice/agent.py` (`tts_node`, `llm_node`); `.venv/.../livekit/agents/metrics/base.py` (LLMMetrics/TTSMetrics); `.venv/.../livekit/agents/voice/events.py` (MetricsCollectedEvent).
- **Verify:** `.venv/bin/python -c "import agent"` imports (openai resolves); grep shows `openai.LLM`, `Agent(instructions=`, `metrics_collected`, no `EchoAgent`.

### Task 7: Tests (offline — no GPU/SFU/network/key)

- **Files:** `infra/pi/agent/tests/test_config.py` (create), `tests/test_soul.py` (create), `tests/test_tokenizer_ru.py` (create), `tests/test_litellm_config.py` (create)
- **Depends on:** Task 2, Task 4, Task 5, Task 6
- **Scope:** L
- **What:** Unit-cover the offline surface.
- **How:** `test_config.py`: monkeypatch LiveKit keys; assert LLM defaults + overrides; missing keys still raise. `test_soul.py`: `_load_soul` returns non-empty text from the real SOUL.md; raises on missing path. `test_tokenizer_ru.py` (req #7): import `livekit.agents.tokenize.blingfire.SentenceTokenizer`; assert correct split of a multi-sentence Russian string on `. ! ?` and NO mis-split of `т.е.`/`т.д.`. `test_litellm_config.py`: YAML-parse `infra/pi/litellm/config.yaml`; assert `model_name`, `model`, `api_base`, `api_key` values.
- **Context:** `infra/pi/agent/tests/conftest.py`, existing `tests/test_*.py`.
- **Verify:** `.venv/bin/python -m pytest tests/ -q` — all green.

### Task 8: Docs

- **Files:** `infra/pi/agent/README.md` (edit)
- **Depends on:** Task 1, Task 2, Task 3, Task 6
- **Scope:** M
- **What:** Document LiteLLM proxy setup, the Ollama key prerequisite, SOUL, model-swap, latency.
- **How:** New section: LiteLLM own-venv install + config path + run command + `OLLAMA_API_KEY` EnvironmentFile + systemd install/enable + worker `After=` ordering. Document the one human prerequisite (Ollama Cloud API key — only for the live run). Document SOUL.md (edit, loaded at session start, fails loudly if missing), the one-line model swap (req #3), and where first-audio latency is logged.
- **Context:** `infra/pi/agent/README.md`.
- **Verify:** Manual read — all of the above present and accurate.

### Task 9: Validation

- **Files:** —
- **Depends on:** all
- **Scope:** M
- **What:** Run the offline suite + import/smoke checks; document the human-gated live acceptance.
- **How:** `from livekit.plugins import openai` import; `from config import load_config`; `import agent`; `pytest tests/ -q`; YAML parse of the litellm config. Flag the live multi-turn Russian conversation (req #6, needs the Ollama key) as the human-gated acceptance step.
- **Verify:** `.venv/bin/python -m pytest tests/ -q` green; all imports resolve.

## Execution

- **Mode:** sub-agents
- **Parallel:** true
- **Reasoning:** Most tasks touch disjoint files and parallelize; only `agent.py` (T6) converges on deps + config + SOUL.
- **Order:**
  Group 1 (parallel): Task 1, Task 2, Task 3, Task 4, Task 5
  ─── barrier ───
  Group 2 (sequential): Task 6
  ─── barrier ───
  Group 3 (parallel): Task 7, Task 8
  ─── barrier ───
  Group 4: Task 9

## Verification

Acceptance criteria (from issue #13):

1. LiteLLM Pi systemd service, OpenAI-compatible, alias `voice-agent`→`nemotron-3-super:cloud` — Task 2, Task 3.
2. Ollama Cloud auth via API key env var, no interactive login — Task 2, Task 3.
3. `openai.LLM` plugin at LiteLLM; model swap is one-line — DD-2, Task 1, Task 2, Task 6.
4. `SOUL.md` (Russian) as system prompt; replies reflect persona — DD-5, Task 5, Task 6.
5. LLM output sentence-chunked into TTS; speaks before full response — DD-1, Task 6 (free via framework).
6. Multi-turn Russian voice conversation e2e; first-audio latency measured + logged — DD-6, Task 6, Task 9 (live, human-gated).
7. Russian sentence tokenization verified — DD-1, Task 7 (regression test).

## Materials

- ADR-0004 — `docs/adr/0004-llm-model.md` (model + latency + LiteLLM swap rationale).
- ADR-0006 — `docs/adr/0006-agent-turn-control.md` (turn control, unchanged here).
- Prior art — `infra/pi/agent/agent.py` (#12 echo wiring), `config.py`, `tts_plugin.py` (DO NOT modify), `deploy/voice-agent-worker.service`.

## Human prerequisite (not a build blocker)

The **Ollama Cloud API key** is absent and must be supplied by the operator (into the `litellm.service` EnvironmentFile as `OLLAMA_API_KEY`). It is required ONLY for the live multi-turn acceptance (req #6); all build steps and offline unit tests proceed without it.
