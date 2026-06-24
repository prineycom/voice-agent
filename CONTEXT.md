---
tags:
  - voice-agent
  - glossary
  - project-context
---

# Voice Agent

Realtime voice communication platform with visual presence. Hybrid architecture: Pi 5 hosts the LiveKit SFU, Agent Worker, LiteLLM, and web/kiosk frontend; Desktop provides GPU-accelerated STT and TTS via WebSocket; cloud LLM provides reasoning via LiteLLM. Hermes MCP supplies tool calling, memory, and skills.

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

**Hermes MCP**:
The MCP server providing tool calling, persistent memory, and skills to the Agent Worker. Runs locally on Pi 5.
_Avoid_: tool server, function server

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
- **Tool calling**: Through [[Hermes MCP]] (HTTP, standalone service) — SOUL.md, memory, skills. Deferred to Agent Worker slice 2; slice 1 is the bare voice loop.
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