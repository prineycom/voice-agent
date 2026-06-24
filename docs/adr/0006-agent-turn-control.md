---
tags:
  - voice-agent
  - adr
status: accepted
---

# Agent Turn Control: LiveKit Owns Endpointing and Interruption

The Agent Worker is built on LiveKit's `AgentSession`, which owns VAD, turn
detection (endpointing), and interruption. The Desktop STT/TTS WebSocket
protocols are *driven by* these LiveKit signals rather than deciding turn
boundaries themselves:

- **Endpointing**: LiveKit's Silero VAD + turn-detector on the Pi decides the
  user has stopped speaking. The STT plugin then sends `{"event":"end"}` to
  flush the final transcript. The Desktop STT's own `vad_filter` only cleans
  audio; it never auto-endpoints on silence (the server has no such code path).
- **Interruption (barge-in)**: when LiveKit's VAD detects the user speaking
  over the agent, the TTS plugin aborts synthesis by **closing the TTS
  WebSocket** — the Desktop TTS server cancels its producer on disconnect.
  There is no in-band stop message in the protocol.
- **Connection lifecycle**: one STT socket per session (`end` flushes+clears
  the server buffer each turn, `reset` discards a turn); a fresh TTS socket per
  turn (so closing it is a clean abort).

## Considered Options

- **LiveKit owns endpointing + interruption** ✅ — reuses LiveKit's tuned
  VAD/turn-detector and interruption handling; keeps the STT buffer bounded
  (avoids the server's O(n²) re-transcribe of a growing buffer); no Desktop
  protocol changes
- **STT server owns endpointing** ❌ — would duplicate VAD logic on the
  Desktop, require new silence-detection code, and split turn control across
  two nodes
- **In-band TTS stop message** ❌ — cleaner than socket-close in theory, but
  requires restructuring the TTS server's WS loop to read control messages
  while streaming; socket-close already triggers the existing producer-cancel
- **Hand-rolled orchestration loop** ❌ — reimplements VAD, endpointing, and
  interruption that `AgentSession` already ships

## Consequences

- The STT plugin must send `{"event":"end"}` on LiveKit's turn-end signal —
  the STT server never finalizes on its own.
- Interruption latency depends on LiveKit's VAD plus WebSocket close
  propagation over Tailscale; in-flight TTS audio already buffered locally is
  dropped, not played.
- A future "in-band cancel" optimization would let the TTS socket persist
  across turns, but is unnecessary while LAN connect cost is negligible vs
  inference time (see [[0003-websocket-stt-tts]]).
- Double VAD by design: LiveKit VAD (turn control, Pi) is complementary to the
  Desktop `vad_filter` (audio cleaning) — not redundant.
