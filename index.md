---
tags:
  - voice-agent
  - index
---

# Voice Agent

Realtime voice communication platform with visual presence.

**Status**: Architecture defined, ready for implementation.

## Overview

Hybrid three-node architecture:
- **Pi 5** — LiveKit SFU, Agent Worker, LiteLLM, Hermes MCP, web/kiosk frontend
- **Desktop (RTX 4070)** — GPU Worker: STT (faster-whisper large-v3-turbo) + TTS (Qwen3-TTS-1.7B)
- **Cloud LLM** — nemotron-3-super via Ollama Cloud (upgradeable)

Audio flows through a cascaded pipeline: STT → LLM → TTS. Each stage is a separate process, connected via LiveKit WebRTC (user ↔ Pi 5) and WebSocket (Pi 5 ↔ Desktop).

## Key Decisions

- Architecture: Hybrid Pi 5 + Desktop + Cloud → [[0001-hybrid-architecture]]
- Transport: LiveKit WebRTC → [[0002-livekit-transport]]
- STT/TTS: WebSocket binary frames → [[0003-websocket-stt-tts]]
- LLM: nemotron-3-super:cloud (MVP, upgradeable) → [[0004-llm-model]]
- Client: Kiosk (physical display) + Web (local network)
- Animation: Live2D (free sample, motion states, no lip-sync)
- Display: Live2D + transcript + tool call visualization
- Sound: LiveKit WebRTC in browser
- Tool calling: Through [[Hermes MCP]]

## Epics

| # | Epic | Description |
|---|------|-------------|
| 1 | LiveKit SFU | Install and configure LiveKit server on Pi 5 |
| 2 | GPU Worker — STT | WebSocket STT service on Desktop (faster-whisper) |
| 3 | GPU Worker — TTS | WebSocket TTS service on Desktop (Qwen3-TTS) |
| 4 | Agent Worker | LiveKit Agent on Pi 5: orchestrates STT → LLM → TTS, Hermes MCP integration |
| 5 | Web Frontend | Live2D + transcript + tool call visualization, kiosk + web access |
| 6 | Kiosk Setup | Fullscreen browser on Pi 5, auto-launch |
| 7 | End-to-End Testing | Latency measurement, quality validation |
| 8 | Audio2Face → Live2D | AI-driven facial animation: Audio2Face-3D blendshapes → Live2D parameters |

See [[epics-overview]] for detailed epic descriptions.

## Research

- [[research/2026-07-03-avatar-video-models]] — Real-time avatar & video models: SoulX-FlashHead, MuseTalk, Audio2Face-3D, EMAGE, ChatAnyone. Architecture options for adding visual avatar to voice-agent.

## Infrastructure

- SSH key: Pi → Desktop (passwordless, password login preserved)
- Tailscale: Pi (priney-pi) ↔ Desktop (100.75.88.35)
- MCP SSH server: mcp-ssh-manager (37 tools) configured in Hermes
- LiteLLM: not yet running on Pi 5
- LiveKit: not yet installed