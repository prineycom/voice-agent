---
tags:
  - voice-agent
  - adr
status: accepted
---

# WebSocket for STT/TTS Transport Between Pi 5 and Desktop

Binary WebSocket frames carry audio between Pi 5 (Agent Worker) and Desktop (GPU Worker). STT receives audio frames and returns JSON text. TTS receives JSON text and returns audio frames. Chosen over gRPC for simplicity, FastAPI native support, and LiveKit ecosystem alignment.

## Considered Options

- **WebSocket (binary frames)** ✅ — simple, FastAPI native, binary audio + JSON text in same connection, LiveKit ecosystem pattern
- **gRPC** ❌ — more complex setup, protobuf schema overhead, unnecessary for 2-node LAN communication
- **HTTP polling** ❌ — too slow for streaming audio
- **Direct TCP** ❌ — reinventing WebSocket

## Consequences

- Desktop runs two WebSocket endpoints: `/stt` (audio in, text out) and `/tts` (text in, audio out)
- Binary frames for audio, JSON for text/metadata
- Connection persists for duration of a conversation turn
- LAN latency (Tailscale) negligible vs model inference time