# CLAUDE.md

## Project

Voice Agent — realtime voice communication platform with visual presence. Hybrid architecture: Pi 5 (LiveKit SFU + Agent Worker + LiteLLM + Hermes + web/kiosk), Desktop (GPU STT/TTS via WebSocket), Cloud LLM via LiteLLM.

## Architecture

```
User (browser/kiosk)
    ↕ LiveKit WebRTC (audio)
Pi 5
├── LiveKit SFU
├── Agent Worker (Python, LiveKit Agents)
│     ├── STT plugin → WebSocket → Desktop
│     ├── LLM → LiteLLM → Cloud (nemotron-3-super)
│     └── TTS plugin → WebSocket → Desktop
├── LiteLLM (LLM proxy)
├── Hermes MCP (tools, memory, skills)
└── Web/Kiosk Frontend (Live2D + transcript + tool viz)
    ↕ WebSocket (binary audio frames)
Desktop (RTX 4070)
├── STT Service (faster-whisper large-v3-turbo, CTranslate2)
└── TTS Service (Qwen3-TTS-1.7B, FasterQwenTTS)
```

## Network

- Tailscale: Pi (priney-pi) ↔ Desktop (100.75.88.35)
- SSH key: Pi → Desktop (passwordless, password login preserved)
- MCP SSH server: mcp-ssh-manager configured in Hermes

## Conventions

- Python for Agent Worker (LiveKit Agents framework is Python-native)
- TypeScript/Vanilla JS for web frontend
- FastAPI for WebSocket services on Desktop
- Commits: conventional commits (feat:, fix:, docs:, chore:)
- All .md files in English

## Non-obvious

- Agent Worker is on Pi 5 (not Desktop) — zero latency on output routing, Hermes local
- STT/TTS transport is WebSocket (not gRPC) — simpler, FastAPI native, LiveKit ecosystem pattern
- LLM model is swappable via LiteLLM config (one line) — now a local Qwen3.5-4B-MTP on the Desktop GPU (llama.cpp, `voice-agent-llm` NSSM on :8004), replacing cloud (ADR 0014). Non-thinking via `LLAMA_CHAT_TEMPLATE_KWARGS` env; MTP self-speculative decoding for speed; `-ub` (not context) is the VRAM lever
- Live2D: motion states (idle/listening/thinking/speaking) + expressions, driven by authoritative agent motion events on the `voiceagent` data channel; expressions come from LLM inline emotion tags (enum neutral/happy/sad/surprised/thinking, stripped before TTS/transcript); volume-based lip-sync on the agent's WebRTC audio track (client-side, not phoneme/TTS-side) — see ADR 0008-0011
- Frontend is vanilla JS as ES modules, no build step (no React/Vue overhead for Pi 5); served as static files by the stdlib http.server
- Sound goes through LiveKit WebRTC in browser, not system speakers
- Kiosk and web access share the same responsive frontend (kiosk-specific concerns are Epic 6)