# Voice Agent — Architecture Overview

> **Project:** [prineycom/voice-agent](https://github.com/prineycom/voice-agent)  
> **Branch:** `epic-4-agent-worker`  
> **Last updated:** 2026-07-16

---

## 1. System Context

Voice Agent — realtime voice communication platform with visual presence. A user speaks into a browser (or kiosk), hears the agent reply, and sees a Live2D avatar with live transcript.

### Key Design Principle: Hybrid Architecture

The system splits across two machines:

| Machine | Role | Hardware |
|---------|------|----------|
| **Pi 5** (edge) | LiveKit SFU, Agent Worker, LiteLLM, Hermes, Web/Kiosk | 8GB RAM, ARM |
| **Desktop** (GPU) | STT (faster-whisper), TTS (Qwen3-TTS) | RTX 4070 |

**Why hybrid?** The Pi 5 is too weak for real-time STT/TTS inference. The Desktop has a GPU but is not always-on. The Pi runs the control plane 24/7; the Desktop provides GPU inference on demand over the local network (Tailscale).

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser / Kiosk (Pi 5 display)                                 │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ Live2D      │  │ Transcript   │  │ Tool Call Viz        │  │
│  │ Avatar      │  │ (chat log)   │  │ (Hermes activity)    │  │
│  └──────┬──────┘  └──────┬───────┘  └──────────┬───────────┘  │
│         │               │                      │              │
│         └───────────────┼──────────────────────┘              │
│                         │ LiveKit WebRTC (audio)               │
└─────────────────────────┼──────────────────────────────────────┘
                          │
┌─────────────────────────┼──────────────────────────────────────┐
│  Pi 5                   │                                      │
│  ┌──────────────────────▼──────────────────────────────┐       │
│  │  LiveKit SFU (port 7880)                            │       │
│  │  WebRTC routing, room management                    │       │
│  └──────────────────────┬──────────────────────────────┘       │
│                         │                                      │
│  ┌──────────────────────▼──────────────────────────────┐       │
│  │  Agent Worker (Python, LiveKit Agents)               │       │
│  │                                                      │       │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐           │       │
│  │  │ STT      │  │ LLM      │  │ TTS      │           │       │
│  │  │ Plugin   │──▶ Plugin   │──▶ Plugin   │           │       │
│  │  │ (WS→    │  │ (LiteLLM │  │ (WS→    │           │       │
│  │  │ Desktop) │  │  /v1)    │  │ Desktop)│           │       │
│  │  └────┬─────┘  └────┬─────┘  └────┬─────┘           │       │
│  │       │             │             │                  │       │
│  │  ┌────▼─────────────▼─────────────▼────────────┐    │       │
│  │  │  hermes_tasks.py (task manager, per-task)     │    │       │
│  │  │  ┌─────────────────────────────────────────┐ │    │       │
│  │  │  │  delegate() → AcpClient → hermes acp    │ │    │       │
│  │  │  │  (long-lived stdio process, supervised) │ │    │       │
│  │  │  │  run_command(args) → literal shell only │ │    │       │
│  │  │  └─────────────────────────────────────────┘ │    │       │
│  │  └──────────────────────────────────────────────┘    │       │
│  └──────────────────────┬──────────────────────────────┘       │
│                         │                                      │
│  ┌──────────────────────▼──────────────────────────────┐       │
│  │  LiteLLM Proxy (port 4000)                          │       │
│  │  OpenAI-compatible → Cloud LLM (nemotron-3-super)   │       │
│  └──────────────────────────────────────────────────────┘       │
│                                                                  │
│  ┌──────────────────────────────────────────────────────┐       │
│  │  Hermes CLI (standalone agent, subprocess)            │       │
│  │  Tools: web, files, terminal, memory, skills,        │       │
│  │  YouTrack, SSH, send_message, vision, cron           │       │
│  └──────────────────────────────────────────────────────┘       │
│                                                                  │
│  ┌──────────────────────────────────────────────────────┐       │
│  │  Web Test-Harness (port 8080)                        │       │
│  │  Serves index.html + mints LiveKit tokens            │       │
│  └──────────────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────┼──────────────────────────────────────┐
│  Desktop (RTX 4070)     │                                      │
│                         │ WebSocket (binary PCM)               │
│  ┌──────────────────────▼──────────────────────────────┐       │
│  │  STT Service (port 8001)                            │       │
│  │  faster-whisper large-v3-turbo (CTranslate2)        │       │
│  │  WS /stt: PCM16@16kHz → JSON transcript             │       │
│  │  GET /health: model_loaded gate                      │       │
│  └──────────────────────────────────────────────────────┘       │
│                         │                                        │
│  ┌──────────────────────▼──────────────────────────────┐       │
│  │  TTS Service (port 8002)                            │       │
│  │  Qwen3-TTS-1.7B (FasterQwenTTS)                     │       │
│  │  WS /tts: JSON text → PCM16@24kHz chunks            │       │
│  │  GET /health: model_loaded gate                      │       │
│  └──────────────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. Component Breakdown

### 2.1 LiveKit SFU (`infra/pi/docker-compose.yml`, `infra/pi/livekit.yaml`)

- **Docker container** (`livekit/livekit-server:v1.13.1`), host networking
- Routes WebRTC audio/video between browser and Agent Worker
- UDP port range 50000–60000 for media, TCP 7880 for signaling, TCP 7881 for TURN fallback
- Single-node, no Redis, no external IP (private network only)
- Keys injected via `LIVEKIT_KEYS` env var from `.env`

### 2.2 Agent Worker (`infra/pi/agent/`)

The core orchestrator. A Python process using the [LiveKit Agents](https://github.com/livekit/agents) framework.

#### Entrypoint: `agent.py` (253 lines)

- Loads config → SOUL.md → Worker Skill → health gates → connects to room
- Creates `AgentSession` with STT + LLM + TTS + local Silero VAD
- Registers `GreetingAgent` (speaks on `on_enter`)
- Spawns the long-lived `hermes acp --accept-hooks` process at startup (supervised: crash → respawn) and wires the task manager (`hermes_tasks.py`) into session userdata for hybrid delegation
- Logs first-audio latency (LLM TTFT, TTS TTFB) via `metrics_collected` hook

**Flow per room join:**
1. `load_config()` — reads `.env` → `AgentConfig` dataclass
2. `_load_soul()` — reads `SOUL.md` (required, fails loud if missing)
3. `_load_text_file(skill)` — reads `skills/hermes.md` (optional, warns if missing)
4. `check_stt_health()` + `check_tts_health()` — GET Desktop `/health`, abort if either is down
5. `silero.VAD.load()` — local ONNX VAD on Pi (no cloud)
6. `ctx.connect()` — join LiveKit room
7. Spawn/attach `AcpClient` (`hermes acp --accept-hooks`, ndjson JSON-RPC over stdio) and create the task manager
8. `AgentSession.start(GreetingAgent(...))` — begin STT→LLM→TTS loop

#### Config: `config.py` (119 lines)

- `AgentConfig` frozen dataclass with all wiring parameters
- Loads from `.env` via `python-dotenv`
- Required keys: `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` (fail fast)
- All others have sensible defaults pointing to localhost/Tailscale

#### STT Plugin: `stt_plugin.py` (177 lines)

- `DesktopSTT(stt.STT)` — non-streaming, one persistent WebSocket per session
- Sends PCM16@16kHz mono → JSON `{"event":"end"}` → reads `{"text","is_final"}`
- Handles resampling (any input rate → 16kHz) via `rtc.AudioResampler`
- **Barge-in:** on `CancelledError`, drops the socket entirely (forces fresh reconnect, clean server buffer)
- On transient errors: sends `{"event":"reset"}` to clear server buffer, then retry

#### TTS Plugin: `tts_plugin.py` (107 lines)

- `DesktopTTS(tts.TTS)` — one-shot, fresh WebSocket per utterance
- Sends JSON `{"text","voice"}` → reads binary PCM16@24kHz chunks → `{"done":true}`
- `ChunkedStream._run()` handles sample alignment (carries trailing odd byte across chunks)
- On interruption: framework cancels the task → `finally` block closes the socket → Desktop cancels producer

#### Health Gate: `health.py` (96 lines)

- `check_stt_health()` / `check_tts_health()` — GET `/health` with 5s timeout
- Requires HTTP 200 + `model_loaded: true`
- `STTHealthError` / `TTSHealthError` with actionable messages

#### ACP Transport: `acp_client.py`

- `AcpConnection` — ndjson JSON-RPC codec over the `hermes acp --accept-hooks` subprocess's stdio
- `AcpClient` — supervisor for the single long-lived process: spawned at worker startup (cold `initialize` ~8s, so never on first delegation), health-checked, respawned on crash; Hermes being down degrades gracefully and never crashes the agent
- `initialize {protocolVersion:1}` → `session/new {cwd, mcpServers}` → `session/prompt {sessionId, prompt}` per task; consumes `session/update` notifications (tool_call / tool_call_update) and answers `session/request_permission` defensively
- One `session/new` per delegated task, up to `max_concurrent` (3) concurrent sessions on the one process

#### Hermes Task Manager: `hermes_tasks.py`

- Task manager: one live per-task state (running / last-tool / step-counter / done / failed), one ACP session per task
- `delegate(request)` — hybrid **8s fast window**: races the ACP result against a timer; ≤8s → reply returned **synchronously as the tool result**; >8s → returns `task_id`, continues in the background
- Milestone narration — ACP `tool_call`/`tool_call_update` events mapped through a template phrase map, spoken via `session.say` (ephemeral: never written to `chat_ctx`, never interrupts the user)
- On completion: appends a **synthetic tool turn** to `chat_ctx` immediately — `assistant{tool_calls:[{name:"task_result",...}]}` + `tool{content:<answer>}` — so the result is a first-class, tool-linked context entry, not a floating system message
- Delivery: waits for a conversational pause (≤15s) to speak the report via `session.generate_reply`; past that, a soft barge-in ("кстати, по той задаче…")
- Failure/timeout (300s cap): same synthetic-tool-turn shape with an honest error report, task state `failed`, **no auto-retry**
- Configurable via `config.yaml` `worker_tools`: `fast_window_s`, `max_concurrent`, `task_timeout_s`, `delivery_fallback_s`
- `cancel(hint)` — cancel by substring or all
- `shutdown()` — cancel everything on room disconnect

#### Worker Tools: `worker_tools.py`

- `@function_tool` adapters exposed to the LLM:
  - `delegate(request)` — any free-form intent/task → routed through `hermes_tasks.py`'s hybrid fast-window path
  - `run_command(cmd: str)` — **literal shell command only** (`git log`, `df -h`); free-form requests ("найди…", "сделай…") are explicitly forbidden here (enforced in the docstring + `skills/hermes.md`), parsed with `shlex` and checked against the `config.yaml` whitelist, run via `asyncio.create_subprocess_exec`, 120s timeout (configurable), kills child on timeout
  - `list_tasks()` — reads the single live per-task state from `hermes_tasks.py`
  - `cancel(hint)` — thin wrapper → task manager's `cancel()`

#### SOUL: `SOUL.md` (41 lines)

- Loaded verbatim as LLM system prompt
- Personality: Priney/Pri — digital alter ego of Pavel
- Russian language, male gender, natural slang, self-irony
- Voice-specific rules: short sentences, no markdown, no emoji, numbers as words

#### Worker Skill: `skills/hermes.md` (45 lines)

- Appended to instructions alongside SOUL
- Teaches LLM the hybrid delegation pattern (fast-window `delegate` vs. literal-only `run_command`)
- Documents `delegate`, `run_command`, `list_tasks`, `cancel`
- Principle: "never do work yourself — always through Hermes"

### 2.3 LiteLLM Proxy (`infra/pi/litellm/config.yaml`)

- Runs on Pi 5, port 4000, as a systemd service
- Single model alias `voice-agent` → `openai/nemotron-3-super:cloud` (Ollama Cloud)
- One-line model swap: change `model:` line
- Master key authentication (client must send `LLM_API_KEY`)
- Systemd: `litellm.service` with `EnvironmentFile` for secrets

### 2.4 Hermes CLI

- Standalone agent installed on Pi 5 (`hermes` CLI)
- Full tool set: web search, files, terminal, memory, skills, YouTrack, SSH, send_message, vision, cron
- Called as async subprocess from Agent Worker
- Session persistence via SQLite (independent of Agent Worker lifecycle)
- Flags: `-Q` (quiet), `--yolo` (auto-confirm), `--source tool`, `--resume <session_id>`

### 2.5 Desktop STT Service (`infra/desktop/stt/`)

| File | Lines | Role |
|------|-------|------|
| `server.py` | 160 | FastAPI app, WS `/stt`, GET `/health` |
| `audio.py` | 14 | `pcm16_to_float32()` conversion |
| `tests/` | — | `conftest.py`, `test_audio.py`, `test_server.py` |

- **Model:** faster-whisper `large-v3-turbo` via CTranslate2 (CUDA, int8_float16)
- **Protocol:** binary PCM16@16kHz → JSON `{"event":"end"}` → interim partials + final transcript
- **Partial results:** every 1s (configurable `STT_PARTIAL_INTERVAL_SEC`), re-transcribes full buffer
- **Reset:** `{"event":"reset"}` clears server buffer (used on error recovery)
- **Health:** returns `model_loaded` bool, HTTP 503 while loading
- **Windows:** CUDA DLL path registration via `_register_cuda_dll_dirs()`

### 2.6 Desktop TTS Service (`infra/desktop/tts/`)

| File | Lines | Role |
|------|-------|------|
| `server.py` | 132 | FastAPI app, WS `/tts`, GET `/health` |
| `synthesize.py` | 72 | Qwen3-TTS adapter, `stream_pcm()` generator |
| `audio.py` | 19 | `float32_to_pcm16()`, `resample_to_24k()` |
| `tests/` | — | `conftest.py`, `test_audio.py`, `test_server.py`, `test_synthesize.py` |

- **Model:** `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` via `FasterQwenTTS`
- **Protocol:** JSON `{"text","voice"}` → binary PCM16@24kHz chunks → `{"done":true}`
- **Speaker:** `aiden` (configurable), language `Russian`
- **Threading:** producer runs in `asyncio.to_thread`, feeds `asyncio.Queue`; `cancel` event for interruption
- **Fallbacks documented:** non-streaming custom voice, voice clone, official `qwen-tts` package

### 2.7 Web Test-Harness (`infra/pi/web/`)

| File | Lines | Role |
|------|-------|------|
| `server.py` | 169 | stdlib HTTP server, token minting |
| `index.html` | 299 | Single-page test UI with Live2D + transcript + VU meter |

- **Token server:** `GET /token?identity=<name>` → signed LiveKit JWT (12h TTL)
- **No build step:** stdlib `http.server` + `livekit.api` for signing
- **Credentials:** reads from Agent Worker's `.env` (single source of truth)
- **Frontend:** dark theme, Live2D avatar, transcript log, connection controls, VU meter

---

## 3. Data Flow: One Conversation Turn

```
User speaks into browser mic
        │
        ▼
LiveKit SFU → WebRTC audio frames
        │
        ▼
Agent Worker receives audio
        │
        ▼
Silero VAD (local ONNX on Pi) detects end of speech
        │
        ▼
DesktopSTT.recognize() sends PCM16@16kHz via WebSocket
        │
        ▼
Desktop STT Service transcribes (faster-whisper, GPU)
        │
        ▼
Returns JSON {"text": "...", "is_final": true}
        │
        ▼
Agent Worker LLM plugin sends transcript + instructions to LiteLLM
        │
        ▼
LiteLLM proxies to Cloud LLM (nemotron-3-super via Ollama Cloud)
        │
        ▼
LLM response stream starts (TTFT logged)
        │
        ▼
DesktopTTS.synthesize() sends text via WebSocket
        │
        ▼
Desktop TTS Service synthesizes (Qwen3-TTS, GPU)
        │
        ▼
Streams PCM16@24kHz chunks back
        │
        ▼
Agent Worker publishes audio to LiveKit room
        │
        ▼
Browser plays audio, shows transcript, animates avatar
```

### Hands-and-Mouth Split (Tool Calling): Hybrid Fast-Window Delegation

Per ADR-0022. `delegate(request)` races the ACP result against an **8s fast
window**; the two branches below diverge only after that timer.

```
User: "Найди погоду в Москве"
        │
        ▼
Agent Worker LLM decides: needs tool
        │
        ▼
Calls delegate("найди погоду в Москве")
        │
        ▼
hermes_tasks.py opens session/new on the long-lived hermes acp process,
sends session/prompt, races the result against 8s
        │
        ├── ≤8s: reply returned synchronously as the tool result
        │        → Agent LLM re-voices it in SOUL style directly, no
        │          further round trip
        │
        └── >8s: tool returns task_id, drops to background
                 │
                 ▼
            Agent says: "Сейчас гляну" (continues conversation)
                 │
                 ▼
            ACP streams tool_call / tool_call_update events →
            milestone narration via session.say (e.g. "смотрю
            погоду…"), never written to chat_ctx, never interrupts
            the user
                 │
                 ▼
            Task completes → hermes_tasks.py immediately appends a
            synthetic tool turn to chat_ctx:
            assistant{tool_calls:[{name:"task_result",...}]} +
            tool{content:<answer>}
                 │
                 ▼
            Waits for a conversational pause (≤15s), then
            session.generate_reply (or a soft barge-in past that
            window)
                 │
                 ▼
            Agent LLM reads the tool-linked result and re-voices it
            in SOUL style: "В Москве сейчас +22, ясно"
```

On failure or the 300s timeout cap, the same synthetic-tool-turn shape
carries an honest error report (task state `failed`); there is no
auto-retry.

---

## 4. Key Architectural Decisions (ADRs)

| # | Decision | File |
|---|----------|------|
| 0001 | Hybrid architecture: Pi 5 + Desktop GPU | [ADR-0001](../adr/0001-hybrid-architecture.md) |
| 0002 | LiveKit WebRTC for audio transport | [ADR-0002](../adr/0002-livekit-transport.md) |
| 0003 | WebSocket binary frames for STT/TTS (not gRPC) | [ADR-0003](../adr/0003-websocket-stt-tts.md) |
| 0004 | LLM model: nemotron-3-super via LiteLLM | [ADR-0004](../adr/0004-llm-model.md) |
| 0005 | Private network + Tailscale TLS (no public exposure) | [ADR-0005](../adr/0005-edge-tls-caddy.md) |
| 0006 | Turn control: LiveKit owns endpointing + interruption | [ADR-0006](../adr/0006-agent-turn-control.md) |
| 0007 | Hermes via CLI subprocess (not MCP) — superseded by 0022 | [ADR-0007](../adr/0007-hermes-cli-delegation.md) |
| 0022 | Hermes delegation v2: long-lived ACP transport + hybrid fast-window delegation | [ADR-0022](../adr/0022-hermes-acp-hybrid-delegation.md) |

---

## 5. File Map

```
voice-agent/
├── CLAUDE.md                          # Project context for AI coding agents
├── CONTEXT.md                         # Glossary + decisions + example dialogue
├── epics-overview.md                  # Epic roadmap
├── index.md                           # Project index
├── README.md                          # Root README
│
├── docs/
│   ├── adr/                           # Architecture Decision Records
│   ├── plans/                         # Implementation plans
│   ├── superpowers/plans/             # Superpowers planning docs
│   └── superpowers/specs/             # Design specifications
│
├── infra/
│   ├── desktop/
│   │   ├── stt/                       # STT service (faster-whisper)
│   │   │   ├── server.py              # FastAPI app, WS /stt, GET /health
│   │   │   ├── audio.py               # pcm16_to_float32
│   │   │   ├── requirements.txt
│   │   │   ├── start-stt.bat
│   │   │   └── tests/
│   │   ├── tts/                       # TTS service (Qwen3-TTS)
│   │   │   ├── server.py              # FastAPI app, WS /tts, GET /health
│   │   │   ├── synthesize.py           # Qwen3-TTS adapter
│   │   │   ├── audio.py               # float32_to_pcm16, resample_to_24k
│   │   │   ├── requirements.txt
│   │   │   ├── start-tts.bat
│   │   │   └── tests/
│   │   ├── scripts/                   # smoke_stt.py, smoke_tts.py
│   │   └── README.md
│   │
│   └── pi/
│       ├── agent/                     # Agent Worker (core orchestrator)
│       │   ├── agent.py               # Entrypoint, AgentSession wiring
│       │   ├── config.py              # AgentConfig dataclass + .env loader
│       │   ├── config.yaml            # Whitelist + Hermes worker_tools limits (fast_window_s, max_concurrent, ...)
│       │   ├── health.py              # STT/TTS startup health gates
│       │   ├── acp_client.py          # AcpConnection codec + AcpClient supervisor (hermes acp process)
│       │   ├── hermes_tasks.py        # Task manager: per-task state, hybrid fast-window delegate
│       │   ├── worker_tools.py        # delegate/run_command/list_tasks/cancel function_tool adapters
│       │   ├── stt_plugin.py          # DesktopSTT plugin
│       │   ├── tts_plugin.py          # DesktopTTS plugin
│       │   ├── SOUL.md                # LLM personality (system prompt)
│       │   ├── skills/hermes.md       # Worker skill (tool usage patterns)
│       │   ├── deploy/                # systemd units
│       │   │   ├── voice-agent-worker.service
│       │   │   └── litellm.service
│       │   ├── tests/                 # 10 test files
│       │   └── requirements.txt
│       ├── litellm/
│       │   └── config.yaml            # LiteLLM model routing
│       ├── web/                       # Test harness
│       │   ├── server.py              # Token server (stdlib)
│       │   └── index.html             # Single-page test UI
│       ├── docker-compose.yml         # LiveKit SFU container
│       ├── livekit.yaml               # SFU config
│       └── .env.example
│
└── .yoke/                             # AI coding agent artifacts
    └── ai/
        ├── epic-1-livekit-sfu/
        ├── epic-4-agent-worker/
        ├── 8-livekit-sfu-compose/
        ├── 11-agent-worker-scaffold-tts-greeting/
        ├── 12-stt-echo-loop/
        ├── 13-litellm-soul/
        ├── 14-barge-in/
        └── 15-hermes-cli-tools/
```

---

## 6. Key Metrics & Constraints

| Metric | Target | Current (nemotron) |
|--------|--------|-------------------|
| Voice-to-Voice Latency | ≤1500ms | ~2200–2500ms |
| LLM TTFT | ≤700ms | ~1600ms |
| TTS TTFB | ≤200ms | ~200ms |
| STT latency | ≤300ms | ~300ms |
| Concurrent Hermes tasks | 3 | 3 |
| Hermes task timeout | 300s | 300s |
| Hermes fast window (sync reply) | 8s | 8s |
| Hermes delivery fallback (pause window) | 15s | 15s |
| run_command timeout | 120s | 120s |

---

## 7. Security Model

- **Private network only** (LAN + Tailscale) — no public exposure
- **TLS** via Tailscale Let's Encrypt certificate on `*.ts.net`
- **LiveKit keys** in `.env` (gitignored), readable by `docker inspect` (acceptable for single-purpose edge node)
- **LiteLLM master key** in systemd `EnvironmentFile` (mode 600)
- **Hermes whitelist** in `config.yaml` — only `hermes` command allowed by default
- **No MCP bridge** — a long-lived `hermes acp` stdio process (Agent Client Protocol, JSON-RPC over stdin/stdout), not a network-accessible tool server
- **STT/TTS health gates** — agent refuses to join room if Desktop services are down

---

## 8. Deployment

### Pi 5 Services

| Service | Port | Type | Start |
|---------|------|------|-------|
| LiveKit SFU | 7880 | Docker | `docker compose up -d` |
| LiteLLM | 4000 | systemd | `systemctl enable --now litellm` |
| Agent Worker | 8090 | systemd | `systemctl enable --now voice-agent-worker` |
| Web Test-Harness | 8080 | manual | `python3 server.py` |

### Desktop Services

| Service | Port | Start |
|---------|------|-------|
| STT (faster-whisper) | 8001 | `start-stt.bat` or `uvicorn server:app` |
| TTS (Qwen3-TTS) | 8002 | `start-tts.bat` or `uvicorn server:app` |

### Network

- Pi 5 ↔ Desktop via Tailscale (`100.75.88.35`)
- SSH key-based auth (passwordless)
- WebSocket binary PCM for STT/TTS
- No gRPC, no HTTP REST for media
