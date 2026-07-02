## Parent

[Epic 5] Web Frontend — Live2D + Transcript + Tool Viz — https://github.com/prineycom/voice-agent/issues/5

## What to build

Refactor the existing single-file **Test harness** (`infra/pi/web/index.html`) into a maintainable
**Web** frontend built from vanilla ES modules — no framework, no build step (see ADR-0011). The
transport concerns (LiveKit connect/token, transcript rendering, tool/ops feed, mic + VU meter,
connection status) are extracted into reusable ES modules so later slices (avatar, motion, lip-sync)
can consume them without touching transport code.

The page gets the new layout decided during grilling, replacing the dev-stand grid: a left
**sidebar** for the tool/ops feed (and status/log), a large central area reserved for the **Avatar**
(empty placeholder in this slice), and a scrolling transcript chat directly below the avatar area.

The stdlib `http.server` (`server.py`) must serve a static directory (multiple JS modules + future
model assets), not just a single `index.html`. All existing behaviour — connect, two-way audio,
streaming transcript, tool/ops visualization, mute, VU meter, connection/agent-state badges — keeps
working in the new shell.

The current `index.html` survives conceptually as the dev **Test harness**; this slice turns it into
the real frontend shell.

## Acceptance criteria

- [ ] `index.html` is split into vanilla ES modules; transport logic (LiveKit/token, transcript, tool+ops feed, mic/VU, status) lives in reusable modules with no build step
- [ ] `server.py` serves a static directory (JS modules + assets), keeping `/token` and `/healthz`
- [ ] New layout: left sidebar (tools/ops), central avatar placeholder, scrolling transcript chat below
- [ ] All prior features still work: connect, two-way audio, streaming transcript, tool/ops feed, mute, VU meter, connection + agent-state indicators
- [ ] No regression in token minting or the `voiceagent` data-channel rendering

## Blocked by

- None — can start immediately (Epics #1 and #4 are in place)
