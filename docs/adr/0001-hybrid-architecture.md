---
tags:
  - voice-agent
  - adr
status: accepted
---

# Hybrid Architecture: Pi 5 + Desktop + Cloud LLM

The voice agent uses a three-node hybrid architecture: Pi 5 hosts the LiveKit SFU, Agent Worker, LiteLLM, and web/kiosk frontend; Desktop (RTX 4070) runs STT and TTS on GPU via WebSocket; cloud LLM provides reasoning through LiteLLM. This splits compute by capability — real-time orchestration on low-power ARM, GPU inference on the desktop, reasoning in the cloud.

## Considered Options

- **Hybrid (Pi 5 + Desktop + Cloud)** ✅ — leverages each node's strength: Pi 5 for low-latency WebRTC, Desktop for GPU, cloud for intelligence
- **All on Pi 5** ❌ — no GPU, STT/TTS would be too slow locally
- **All on Desktop** ❌ — Agent Worker needs to be on Pi 5 for zero-latency output routing and local Hermes access
- **Speech-to-speech cloud model** ❌ — no audit trail, tool calling unreliable, opaque pipeline

## Consequences

- Three network hops: Pi 5 ↔ Desktop (WebSocket), Pi 5 ↔ Cloud LLM (HTTPS)
- SSH key auth configured Pi → Desktop (passwordless, password login preserved)
- Tailscale network connects Pi and Desktop (desktop: 100.75.88.35)
- MCP SSH server (mcp-ssh-manager, 37 tools) configured for Desktop management