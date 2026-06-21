---
tags:
  - voice-agent
  - adr
status: accepted
---

# LiveKit as WebRTC Transport

LiveKit chosen as the WebRTC SFU for audio transport between user and Agent Worker. Provides VAD (voice activity detection), turn detection, interruption handling, and streaming audio — all built-in. Eliminates the need to build custom WebRTC infrastructure.

## Considered Options

- **LiveKit** ✅ — mature SFU, built-in VAD/turn detection, Agent framework, WebRTC streaming, kiosk browser support
- **Raw WebRTC** ❌ — would need to build VAD, turn detection, interruption handling from scratch
- **Daily.co / Retell** ❌ — managed services, less control, vendor lock-in
- **Pipecat** ❌ — orchestration framework only, still needs a transport layer

## Consequences

- LiveKit server runs on Pi 5
- Agent Worker uses LiveKit Agents framework for audio subscription and publishing
- Browser (kiosk) connects as a LiveKit participant
- Audio flows: browser → LiveKit SFU → Agent Worker → (STT → LLM → TTS) → Agent Worker → LiveKit SFU → browser