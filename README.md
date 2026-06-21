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

## Running the LiveKit SFU (Pi 5)

The SFU runs via Docker Compose from `infra/pi/`. First-time setup generates an API
key/secret pair into a gitignored `.env`:

```bash
cd infra/pi
cp .env.example .env
# generate a key/secret pair into .env
sed -i "s|^LIVEKIT_API_KEY=.*|LIVEKIT_API_KEY=API$(openssl rand -hex 6)|" .env
sed -i "s|^LIVEKIT_API_SECRET=.*|LIVEKIT_API_SECRET=$(openssl rand -base64 36 | tr -d '\n')|" .env
```

Lifecycle:

```bash
cd infra/pi
docker compose up -d        # start
docker compose logs -f      # follow logs
docker compose ps           # status
docker compose down         # stop
```

Health check: `curl http://localhost:7880/` returns `OK`. The SFU listens on 7880
(signaling), 7881 (RTC/TCP), and 50000-60000/udp (RTC media), reachable over LAN and
Tailscale. `restart: unless-stopped` brings it back after a reboot.

### Smoke test (two participants, audio)

Prerequisites: `ffmpeg` (`sudo apt install -y ffmpeg`) and the LiveKit CLI. Pin the CLI to
the version validated against server v1.13.1 rather than piping the latest installer:

```bash
# install the LiveKit CLI once — pinned (tested with lk v2.16.6)
curl -sSL "https://github.com/livekit/livekit-cli/releases/download/v2.16.6/lk_2.16.6_linux_arm64.tar.gz" \
  | tar xz -C /tmp && sudo mv /tmp/lk /usr/local/bin/lk

# generate a test tone
ffmpeg -y -f lavfi -i "sine=frequency=440:duration=8" -c:a libopus -b:a 48k /tmp/tone.ogg
```

Open **two shells**. In each, load the credentials and target URL before running `lk`
(both `lk` invocations need `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET`):

```bash
# run this in BOTH shells
cd infra/pi && set -a && . ./.env && set +a
export LIVEKIT_URL="ws://localhost:7880"   # or ws://<tailscale-ip>:7880

# shell 1 — subscriber
lk room join --identity sub --auto-subscribe smoke-room
# shell 2 — publisher
lk room join --identity pub --publish /tmp/tone.ogg --exit-after-publish smoke-room
```

The subscriber logs `track subscribed {kind: audio, ...}` when it receives the publisher's
audio — confirming end-to-end publish/subscribe through the SFU.

## Status

Architecture defined. See [epics overview](epics-overview.md) for implementation plan.

Epic 1 (LiveKit SFU) — server running on Pi 5; browser TLS path tracked in #9.

## Documentation

- [CONTEXT.md](CONTEXT.md) — glossary and project context
- [docs/adr/](docs/adr/) — architecture decision records
- [epics-overview.md](epics-overview.md) — implementation epics breakdown