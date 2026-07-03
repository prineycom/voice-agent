---
tags:
  - voice-agent
  - epics
---

# Epics Overview

Detailed breakdown of implementation epics for the Voice Agent platform.

## Epic 1: LiveKit SFU on Pi 5

**Goal**: Install and configure LiveKit server as the WebRTC SFU on Pi 5.

**Scope**:
- Install LiveKit server (Docker or binary)
- Configure LiveKit API keys and room settings
- Verify WebRTC connectivity from browser on local network
- Test audio publish/subscribe between two participants

**Decisions**: → [[0002-livekit-transport]]

**Estimate**: ~4 чч

---

## Epic 2: GPU Worker — STT WebSocket Service

**Goal**: WebSocket service on Desktop that accepts audio frames and returns transcribed text using faster-whisper large-v3-turbo.

**Scope**:
- Install faster-whisper (CTranslate2) on Desktop with GPU support
- Download large-v3-turbo model (~2.5GB VRAM)
- FastAPI WebSocket endpoint `/stt` — binary audio frames in, JSON text out
- Streaming partial transcripts as they arrive
- Russian language optimization

**Decisions**: → [[0003-websocket-stt-tts]]

**Estimate**: ~6 чч

---

## Epic 3: GPU Worker — TTS WebSocket Service

**Goal**: WebSocket service on Desktop that accepts text and returns audio using Qwen3-TTS-1.7B via FasterQwenTTS.

**Scope**:
- Install Qwen3-TTS-1.7B + FasterQwenTTS on Desktop with GPU support (~4GB VRAM)
- FastAPI WebSocket endpoint `/tts` — JSON text in, binary audio frames out
- Streaming audio chunks (first chunk target: ~250-300ms)
- Russian voice configuration

**Decisions**: → [[0003-websocket-stt-tts]]

**Estimate**: ~6 чч

---

## Epic 4: Agent Worker on Pi 5

**Goal**: LiveKit Agent Worker process on Pi 5 that orchestrates the full voice pipeline.

**Scope**:
- LiveKit Agents framework setup on Pi 5
- Custom STT plugin: connects to Desktop WebSocket `/stt`
- Custom TTS plugin: connects to Desktop WebSocket `/tts`
- LLM integration via LiteLLM (nemotron-3-super:cloud)
- Hermes MCP integration for tool calling, memory, skills
- VAD and turn detection via LiveKit
- Interruption handling (user speaks while agent responds)

**Decisions**: → [[0001-hybrid-architecture]], → [[0004-llm-model]]

**Estimate**: ~12 чч

---

## Epic 5: Web Frontend

**Goal**: Web frontend with Live2D avatar, live transcript, and tool call visualization. Serves both kiosk and local network access.

**Scope**:
- LiveKit client SDK in browser for audio I/O
- Live2D Cubism SDK integration (free sample model)
- Motion states: idle, listening, thinking, speaking
- Live transcript panel (user + agent messages)
- Tool call visualization (shows when agent calls Hermes MCP tools)
- Responsive layout for kiosk (fullscreen) and web access

**Estimate**: ~10 чч

---

## Epic 6: Kiosk Setup on Pi 5

**Goal**: Physical kiosk display on Pi 5 — fullscreen browser auto-launching the web frontend.

**Scope**:
- Configure Pi 5 display output (HDMI)
- Auto-launch browser in kiosk mode on boot
- Connect to LiveKit room automatically
- Audio output via LiveKit WebRTC (not system speakers)

**Estimate**: ~3 чч

---

## Epic 7: End-to-End Testing

**Goal**: Validate full pipeline latency and quality.

**Scope**:
- Measure voice-to-voice latency (target: ≤2.5s with nemotron, ≤1.5s with Gemini Flash)
- Test interruption handling
- Test tool calling through Hermes MCP
- Test Russian language quality (STT accuracy, TTS naturalness)
- Test kiosk display (Live2D motion states, transcript, tool calls)

**Estimate**: ~4 чч

---

## Epic 8: Audio2Face-3D → Live2D Facial Animation

**Goal**: Replace volume-based lip sync with AI-driven phoneme-level facial animation using NVIDIA Audio2Face-3D (open source), mapped to Live2D standard parameters.

**Scope**:
- Deploy Audio2Face-3D NIM on Desktop (gRPC, 0.6 GB VRAM)
- Python FastAPI WebSocket proxy (gRPC → browser)
- ARKit blendshape → Live2D parameter mapping (~50 lines JS)
- Replace volume-based lip sync with blendshape-driven mouth/eyes/brows
- Stream blendshapes via LiveKit DataChannel (~6 KB/s)
- Fallback to volume-based lip sync if Audio2Face is down
- Keep existing motion states (idle/listening/thinking/speaking)

**Research**: → [[research/2026-07-03-avatar-video-models]] (Section 9: ARKit → Live2D mapping)

**GitHub**: [#33](https://github.com/prineycom/voice-agent/issues/33)

**Estimate**: ~18-30 чч

---

## Total Estimate

~63-75 чч (человеко-часов)