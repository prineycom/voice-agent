## Parent

[Epic 4] Agent Worker on Pi 5 — https://github.com/prineycom/voice-agent/issues/4

## What to build

The foundational scaffold of the **Agent Worker** and the first end-to-end proof
of its **output audio path**. A Python Agent Worker runs on the Pi 5 (its own
venv, managed as a systemd service), registers with the LiveKit SFU, and joins a
room on dispatch. On join it speaks a fixed Russian greeting through the custom
**TTS plugin** — a thin `livekit.plugins.tts.TTS` adapter over the GPU Worker's
`/tts` WebSocket (JSON `{text, voice}` in; PCM16 @24kHz out, then `{"done":true}`).
TTS audio is resampled to LiveKit's rate via the framework's built-in resampling
and published into the room, so a participant hears the greeting.

No STT, no LLM yet — this slice isolates deployment, worker registration, room
join, the TTS plugin/protocol, and the output audio path.

Decisions this realizes (from grilling + ADRs):
- Built on LiveKit **AgentSession**, not a hand-rolled loop.
- Deployed as a **venv + systemd** service (SFU stays in Docker).
- **Russian-only** MVP; greeting and TTS voice are Russian.
- **Framework-native resampling** (AudioSource @24kHz → LiveKit), no hand-rolled DSP.
- **Health-gate** TTS `/health` at startup; if unavailable, log + surface a clear
  error rather than failing silently.

## Acceptance criteria

- [ ] Agent Worker runs from a Pi venv under a systemd unit (start/stop/restart, logs)
- [ ] Worker registers with the LiveKit SFU and joins a room when dispatched
- [ ] Custom TTS plugin connects to the Desktop `/tts` WebSocket and streams PCM16 @24kHz
- [ ] A participant joining the room hears a fixed Russian greeting, correctly resampled (no pitch/speed artifacts)
- [ ] Startup checks TTS `/health`; an unavailable GPU Worker yields a clear logged error, not a silent hang
- [ ] README documents venv setup, the systemd unit, and how to dispatch/join for the smoke test

## Blocked by

- Epic 1 — LiveKit SFU on Pi 5 (#1) must be running
- Epic 3 — TTS service on Desktop must be reachable over Tailscale
