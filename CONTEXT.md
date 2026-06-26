---
tags:
  - voice-agent
  - glossary
  - project-context
---

# Voice Agent

Realtime voice communication platform with visual presence. Hybrid architecture: Pi 5 hosts the LiveKit SFU, Agent Worker, LiteLLM, and web/kiosk frontend; Desktop provides GPU-accelerated STT and TTS via WebSocket; cloud LLM provides reasoning via LiteLLM. Hermes (CLI subprocess) is the agent's hands — it supplies tool calling, memory, skills, file/web/terminal access, and third-party services.

## Language

**Agent Worker**:
The process on Pi 5 that orchestrates the voice pipeline — receives audio from LiveKit, sends it to Desktop STT, routes the transcript to the LLM, streams LLM output to Desktop TTS, and publishes audio back through LiveKit. Not the LLM itself. Built on LiveKit's [[AgentSession]], not a hand-rolled loop.
_Avoid_: agent, assistant, voice bot

**AgentSession**:
LiveKit Agents' high-level voice pipeline primitive. Owns VAD, [[Endpointing]], [[Interruption]], and STT→LLM→TTS wiring. The Agent Worker uses it rather than reimplementing the loop; the custom STT/TTS plugins are thin adapters over the Desktop WebSocket protocols.
_Avoid_: pipeline agent, voice pipeline, runner

**Endpointing**:
Deciding the user has stopped speaking and the turn is over. Owned by LiveKit (Silero VAD + turn-detector on Pi). When LiveKit declares the turn ended, the STT plugin sends `{"event":"end"}` to flush the final transcript. The GPU Worker's own `vad_filter` only cleans audio — it does not own turn boundaries.
_Avoid_: turn detection, VAD, silence detection

**Interruption** (barge-in):
The user speaks while the agent is responding. LiveKit detects it via VAD; the Agent Worker aborts TTS by **closing the TTS WebSocket** (the GPU Worker cancels its producer on disconnect — there is no in-band stop message).
_Avoid_: barge-in (use as parenthetical only), cancel, cutoff

**SFU (Selective Forwarding Unit)**:
The LiveKit server component that routes WebRTC audio/video streams between participants. Runs on Pi 5.
_Avoid_: media server, relay

**GPU Worker**:
The Desktop machine (RTX 4070) running STT and TTS models. Receives audio over WebSocket from the Agent Worker and returns text (STT) or audio (TTS).
_Avoid_: inference server, backend

**STT (Speech-to-Text)**:
faster-whisper large-v3-turbo running on the GPU Worker via CTranslate2. Converts incoming audio to text over WebSocket.
_Avoid_: ASR, transcription service, whisper

**TTS (Text-to-Speech)**:
Qwen3-TTS-1.7B running on the GPU Worker via FasterQwenTTS. Converts LLM text output to audio, streamed back over WebSocket.
_Avoid_: voice synthesis, speech engine

**Kiosk**:
A fullscreen browser on Pi 5 displaying the Live2D avatar, live transcript, and tool call visualization. Physical display attached to Pi 5.
_Avoid_: display, screen, dashboard

**Live2D**:
A 2D animation framework for the avatar. Free sample model, motion states (idle, listening, thinking, speaking). No lip-sync — animation states only.
_Avoid_: avatar, character, 3D model

**LiteLLM**:
Proxy running on Pi 5 that routes LLM requests to cloud providers (Ollama Cloud, Google AI Studio, OpenAI). Provides a unified OpenAI-compatible API endpoint.
_Avoid_: LLM gateway, model router

**Hermes**:
A standalone agent application already installed on Pi 5 (`hermes` CLI). It is the **hands** of the voice agent — it does web search, file work, computer use, memory, skills, and anything requiring tools the voice agent itself lacks. The [[Agent Worker]] reaches it by shelling out to the `hermes` CLI as an async subprocess (`asyncio.create_subprocess_exec`, non-blocking to the event loop; no MCP, no messaging bridge). Hermes does reasoning and acts on its side; the worker never touches Hermes-internal state.
_Avoid_: tool server, function server, MCP server, skill server

**Hands-and-Mouth Split**:
The division of labour: the [[Agent Worker]] is the **mouth** — it carries the live conversation and routes every task it cannot itself do to Hermes via [[run_command]]. Hermes is the **hands** — it has memory, web search, files, computer use, skills, and third-party services. The voice agent owns no tools except the single [[Hermes Tools|whitelisted run_command tool]]. Per turn the worker's LLM decides: if the turn needs a tool it delegates to Hermes; if it's pure conversation it answers directly. The worker may lightly re-voice a Hermes result in SOUL style but never does the underlying work itself.
_Avoid_: sub-agent, backend, tool layer

**Hermes Tools** (whitelisted CLI tool + skill):
The [[Agent Worker]] exposes ONE generic `livekit.agents.function_tool` to its LLM: [[run_command]](`args: str`) — which runs any whitelisted CLI command on the Pi as an async subprocess. The whitelist lives in `infra/pi/agent/config.yaml` (`worker_tools.allowed_commands`, default `["hermes"]`) and is enforced by checking `shlex.split(args)[0]` against it. A skill file (`infra/pi/agent/skills/hermes.md`) describes the command patterns (delegate task, list sessions, send message) and is appended verbatim to the Agent instructions alongside SOUL.md at startup. Any future `hermes` subcommand works without code changes — add the command to the whitelist + a pattern to the skill and restart. No MCP, no messaging bridge.
_Avoid_: hermes functions, hermes API, MCP tools

**run_command**:
`run_command(args: str) -> str` — a `livekit.agents.function_tool` that parses `args` with `shlex`, checks `args[0]` against `worker_tools.allowed_commands` (config.yaml), and runs the command via `asyncio.create_subprocess_exec` (async, non-blocking to the event loop), returning stdout. The LLM composes the full CLI args (e.g. `"hermes chat -q '...' -Q --yolo --source tool --resume <sid>"`). Non-whitelisted commands are rejected before exec; a missing command returns a clear error string so the agent degrades gracefully and keeps talking.
_Avoid_: shell, exec, subprocess tool

**Worker Skill**:
A Markdown file (`infra/pi/agent/skills/hermes.md`) teaching the LLM how to compose `run_command` args for Hermes CLI patterns (delegate task, list sessions, send message) plus the "any future hermes subcommand works" principle. Read at startup and concatenated to the Agent `instructions` string alongside [[SOUL.md]] — same loading pattern. Extension is declarative: add a command to `worker_tools.allowed_commands` + a pattern here, restart the worker.
_Avoid_: system prompt, tool docs

**Hermes Session ID**:
The handle Hermes returns on every `run_command("hermes chat ...")` call (a `session_id:` line folded in from stderr in -Q mode, e.g. `20260625_114014_15b9a3`). Passed back via `--resume <id>` to keep one continuous Hermes dialog across many voice turns. Persists on Hermes's on-disk SQLite session store, independent of the [[Agent Worker]] lifecycle.

**Cascaded Pipeline**:
The architecture where audio flows through discrete stages: STT → LLM → TTS. Each stage is a separate process. Chosen over speech-to-speech for auditability and tool-calling reliability.
_Avoid_: pipeline, chain, waterfall

**Voice-to-Voice Latency**:
Time from user finishing speech to agent starting to speak. Composed of STT + LLM TTFT + TTS first chunk. **≤1500ms is the north-star target the LLM upgrade (GPT-4.1/Gemini Flash) must hit, not an MVP acceptance gate.** The nemotron MVP runs ~2.2–2.5s ("walkie-talkie"), see [[0004-llm-model]]. Sentence chunking (speak after sentence #1) is what makes the target reachable post-upgrade.
_Avoid_: response time, round-trip time

**Edge Proxy**:
The single TLS-terminating entry point on Pi 5 (Caddy). Holds the Tailscale certificate, fronts LiveKit signaling (WSS), and later serves the static frontend. "Edge" = closest hop to the user, not a CDN edge.
_Avoid_: gateway, load balancer, ingress

**Tailnet Domain**:
The `*.ts.net` hostname for Pi 5 (e.g. `priney-pi.<tailnet>.ts.net`) for which Tailscale provisions a real Let's Encrypt certificate. Distinct from the LAN hostname/IP, which has no valid public cert.
_Avoid_: domain, hostname, URL

**Secure Context**:
The browser requirement (HTTPS or `localhost`) without which `getUserMedia` — microphone access — is blocked. Both the frontend origin and the LiveKit WSS endpoint must be served over a trusted certificate.
_Avoid_: HTTPS, SSL

## Decisions

- **Architecture**: Hybrid — Pi 5 (SFU + Agent Worker + LiteLLM + Hermes + web/kiosk), Desktop (GPU Worker), cloud LLM. → [[0001-hybrid-architecture]]
- **Transport**: LiveKit WebRTC for audio between user and Agent Worker. → [[0002-livekit-transport]]
- **Access & TLS**: Private network only (LAN + Tailscale), no public exposure. TLS via Tailscale cert on the [[Tailnet Domain]], terminated at a Caddy [[Edge Proxy]]. → [[0005-edge-tls-caddy]]
- **STT/TTS transport**: WebSocket binary frames between Pi 5 and Desktop. → [[0003-websocket-stt-tts]]
- **LLM**: Start with nemotron-3-super:cloud (Ollama Cloud), experiment later. Fastest available (~1.6s TTFT), tool calling supported.
- **Turn control**: LiveKit owns endpointing + interruption; STT/TTS protocols driven by end/reset events and socket-close. → [[0006-agent-turn-control]]
- **Hermes integration**: The Agent Worker reaches Hermes via one generic whitelisted CLI tool ([[run_command]]) + a [[Worker Skill]] describing patterns. Hermes runs with full CLI tools (web, files, terminal, memory, skills, YouTrack, SSH, send_message, vision, cron, everything). Hands-and-mouth split unchanged: the worker only talks and delegates; re-voicing is the LLM's job in-turn. Transport = subprocess exec, whitelist in config.yaml. → [[0007-hermes-cli-delegation]]
- **Client**: Kiosk (physical display on Pi 5) + web access in local network.
- **Animation**: Live2D free sample, motion states only, no lip-sync.
- **Sound**: Via LiveKit WebRTC in browser (Pi 5 kiosk browser fullscreen).
- **Display**: Live2D + transcript + tool call visualization.

## Flagged ambiguities

- **"Agent"** — overloaded term. Canonical: "Agent Worker" (the orchestrator process). The LLM is a cloud model, not "the agent."
- **"Voice"** — overloaded. Canonical: "Voice-to-Voice Latency" for the full pipeline metric. "Audio" for raw sound.

## Example Dialogue

> **Паша**: "Какая задержка до первого токена у nemotron?"
> **При**: *Researches Ollama Cloud benchmarks* → "~1.6 секунды TTFT. Для voice agent это на грани — таргет ≤700ms, но для MVP терпимо. Позже можно переключить на Gemini 2.5 Flash через прямой API ключ в LiteLLM — там 450ms."
> **Паша**: "Ладно, берём nemotron, потом экспериментируем."
> **При**: "Принято. Модель в LiteLLM — одна строка конфига, переключить быстро."