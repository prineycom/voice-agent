---
tags:
  - voice-agent
  - frontend
  - test-harness
---

# Web Test Harness — Design

A minimal, single-file web page to drive the Voice Agent end-to-end for testing:
join the LiveKit room from a browser, talk, hear the agent, and watch
transcript / state / mic-level / latency. Deliberately NOT the full Epic 5
frontend (no Live2D, no tool visualization, no responsive kiosk layout).

## Goal

Open one HTTPS URL on a tailnet device → **Connect** → agent greets in Russian
→ speak → see your STT transcript and the agent's reply, hear its voice, see a
best-effort voice-to-voice latency number.

## Components

### `infra/pi/web/server.py` (stdlib `http.server`, no new deps)
- Binds `127.0.0.1:8080`.
- `GET /` → serves `index.html`.
- `GET /token?identity=<name>` → mints a LiveKit JWT via `livekit.api.AccessToken`
  (key/secret read from `infra/pi/agent/.env`), room fixed to `test`, returns
  `{"token": ..., "url": ...}` where `url` is the public WSS endpoint.
- Run as a transient `systemd-run --user` unit (same pattern as the worker).

### `infra/pi/web/index.html` (vanilla JS, `livekit-client@2` from CDN)
- **Connect/Disconnect** button → `fetch('/token')` → `Room.connect(url, token)` →
  publish mic → subscribe to agent audio (autoplay an `<audio>` element).
- **Transcript** — user + agent lines via the `lk.transcription` text stream
  (the agent's RoomIO already publishes these), distinguished by sender identity.
- **Agent state** — `listening / thinking / speaking` from the `lk.agent.state`
  participant attribute (`ParticipantAttributesChanged`), plus connection state
  and errors.
- **Mic** — VU meter via Web Audio `AnalyserNode` on the local track + mute toggle.
- **Latency** — best-effort: from the final *user* transcript to the first *agent*
  transcript/audio. Approximate; enough to sanity-check the ≤1.5s target.

### TLS / access (`tailscale serve`, two ports, one domain)
- `https://rpi.darter-smoot.ts.net` (443) → frontend+token server (`:8080`).
- `wss://rpi.darter-smoot.ts.net:8443` (8443) → LiveKit (`:7880`).
- The page is a secure context (HTTPS) so `getUserMedia` works; the SDK connects
  to the WSS endpoint on 8443.

### Agent fix (`infra/pi/agent/agent.py`)
`wait_for_participant()` hung in a live test even though the participant was
present and its mic was being read, so the greeting never played. Replace it
with the documented pattern: greet from `Agent.on_enter()` via `session.say()`.
Removes the fragile wait and the dependency on participant-state timing.

## Out of scope
Auth (tailnet is private), recording, room/model selection, mobile layout,
Live2D, tool visualization — all deferred to the full Epic 5 frontend.

## Definition of done
HTTPS page on a tailnet laptop → Connect → Russian greeting → speak → transcript
(user + agent) + audio reply + a latency reading.
