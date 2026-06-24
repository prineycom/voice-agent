## Parent

[Epic 4] Agent Worker on Pi 5 — https://github.com/prineycom/voice-agent/issues/4

## What to build

The **input audio path** and full duplex turn-taking, proven via an echo loop.
Add the custom **STT plugin** — a `livekit.plugins.stt.STT` adapter over the GPU
Worker's `/stt` WebSocket (binary PCM16 @16kHz in; `{"event":"end"}` flushes the
final transcript, `{"event":"reset"}` clears; emits `{text, is_final, partial}`).
With AgentSession's VAD + turn-detector enabled, the agent listens, and when
**LiveKit declares the turn ended** (Endpointing), the plugin sends
`{"event":"end"}` to flush the final transcript and the agent speaks that
transcript straight back through the existing TTS plugin — an echo, no LLM.

This isolates mic capture, downsampling to 16kHz, the STT plugin/protocol, the
LiveKit-owned endpointing contract, and the conversation turn lifecycle.

Decisions this realizes (from grilling + ADR-0006):
- **LiveKit owns endpointing** — the Desktop STT's `vad_filter` only cleans audio;
  the plugin sends `end` on LiveKit's turn-end signal (the STT server never
  auto-finalizes).
- **One STT socket per session**: `end` flushes+clears the buffer each turn,
  `reset` discards a turn — keeps the server buffer bounded (avoids its O(n²)
  re-transcribe of a growing buffer).
- **Framework-native resampling**: mic → 16kHz before STT.
- Health-gate STT `/health` at startup alongside TTS.

## Acceptance criteria

- [ ] Custom STT plugin connects to `/stt`, streams mic audio as PCM16 @16kHz, and consumes `{text, is_final, partial}`
- [ ] AgentSession VAD + turn detection drives endpointing; the plugin sends `{"event":"end"}` only on LiveKit's turn-end signal
- [ ] A single STT socket persists for the session; `end` clears the buffer per turn and `reset` discards a turn
- [ ] Spoken Russian phrases are transcribed and spoken back (echo) within one turn, hands-free
- [ ] Startup health-gates STT `/health`; an unavailable STT yields a clear logged error
- [ ] Mic audio is correctly downsampled to 16kHz (transcripts are accurate, not garbled by rate mismatch)

## Blocked by

- #11 (Slice 1 — Agent Worker scaffold + TTS greeting)
- Epic 2 — STT service on Desktop must be reachable over Tailscale
