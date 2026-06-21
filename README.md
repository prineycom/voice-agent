# Voice Agent

Realtime voice communication platform with visual presence.

## Architecture

Hybrid three-node architecture:
- **Pi 5** — LiveKit SFU, Agent Worker, LiteLLM, Hermes MCP, web/kiosk frontend
- **Desktop (RTX 4070)** — GPU Worker: STT (faster-whisper large-v3-turbo) + TTS (Qwen3-TTS-1.7B)
- **Cloud LLM** — nemotron-3-super via Ollama Cloud (upgradeable)

Audio flows through a cascaded pipeline: STT → LLM → TTS, connected via LiveKit WebRTC (user ↔ Pi 5) and WebSocket (Pi 5 ↔ Desktop).

## Components

| Component | Location | Tech |
|-----------|----------|------|
| LiveKit SFU | Pi 5 | LiveKit server |
| Agent Worker | Pi 5 | Python, LiveKit Agents framework |
| LiteLLM | Pi 5 | Proxy to cloud LLM providers |
| Hermes MCP | Pi 5 | Tool calling, memory, skills |
| STT Service | Desktop | faster-whisper, CTranslate2, FastAPI WebSocket |
| TTS Service | Desktop | Qwen3-TTS-1.7B, FasterQwenTTS, FastAPI WebSocket |
| Web Frontend | Pi 5 | LiveKit client SDK, Live2D Cubism SDK, browser |
| Kiosk | Pi 5 | Fullscreen browser, auto-launch |

## Network

- Pi 5 ↔ Desktop: Tailscale (desktop: 100.75.88.35)
- Pi 5 ↔ Desktop data: WebSocket (binary audio frames)
- User ↔ Pi 5: LiveKit WebRTC
- Pi 5 ↔ Cloud LLM: HTTPS via LiteLLM

## Status

Architecture defined. See [epics overview](epics-overview.md) for implementation plan.

## Documentation

- [CONTEXT.md](CONTEXT.md) — glossary and project context
- [docs/adr/](docs/adr/) — architecture decision records
- [epics-overview.md](epics-overview.md) — implementation epics breakdown