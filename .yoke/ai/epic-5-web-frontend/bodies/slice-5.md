## Parent

[Epic 5] Web Frontend — Live2D + Transcript + Tool Viz — https://github.com/prineycom/voice-agent/issues/5

## What to build

Make the **Web** frontend universally responsive across desktop and mobile, and polish it. The
layout must work from a wide desktop down to a narrow phone: on small screens everything becomes more
compact and the tool/ops **sidebar** collapses into a toggleable overlay rather than a fixed column,
while the **Avatar** and the transcript chat remain the focus.

This slice also covers final polish: connection-status and agent-state indicators, the audio-level
(VU) meter, and the transcript/tool feed all stay legible and correctly placed at every breakpoint.

**Kiosk** mode is explicitly out of scope here — it is handled by Epic 6. The design stays
kiosk-friendly (responsive, fullscreen-capable) but no kiosk-specific auto-connect or Chromium flags
are added.

## Acceptance criteria

- [ ] Layout adapts cleanly from wide desktop to narrow mobile with no broken/overflowing panels
- [ ] On small screens the sidebar collapses into a toggleable overlay; avatar + transcript stay primary
- [ ] Connection status, agent-state, and the VU meter remain visible and legible at all breakpoints
- [ ] Transcript and tool/ops feed stay readable and scrollable on mobile
- [ ] No kiosk-specific code added (deferred to Epic 6); design remains kiosk-friendly

## Blocked by

- #27 — Live2D avatar + motion states (lk.agent.state)
