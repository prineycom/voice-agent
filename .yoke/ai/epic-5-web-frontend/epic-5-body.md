## Goal
Web frontend with a Live2D avatar, live transcript, and tool-call visualization, served to a
desktop/mobile browser on the LAN/tailnet. Built on the existing test harness
(`infra/pi/web/index.html`), which already provides the transport layer (LiveKit audio, streaming
transcript, tool/ops feed, mic + VU meter, connection/agent-state indicators).

> Refined via grilling — see `.yoke/context.md` (glossary) and `docs/adr/0008`–`0011`.

## Scope
- [ ] Refactor the single-file harness into vanilla ES modules, no build step (no React/Vue) — ADR-0011
- [ ] New layout: left sidebar (tool/ops feed), large central avatar, scrolling transcript chat below
- [ ] Live2D avatar via pixi-live2d-display + Cubism Core (CDN), free Cubism sample model — ADR-0010
- [ ] Motion states (idle/listening/thinking/speaking) driven by authoritative agent motion events
- [ ] Cross-component: Agent Worker publishes motion events on the `voiceagent` data channel + LLM
      inline emotion tags (parsed/stripped before TTS and transcript), expression enum
      neutral/happy/sad/surprised/thinking — ADR-0009
- [ ] Volume-based lip-sync on the agent's WebRTC audio track (client-side, not phoneme/TTS) — ADR-0008
- [ ] Live transcript panel (user + agent, streaming) — reuse existing
- [ ] Tool-call / background-ops visualization — reuse existing
- [ ] Responsive desktop + mobile: compact layout, sidebar collapses to a toggleable overlay
- [ ] Microphone permission handling, connection status indicator, audio level meter — reuse existing

## Out of scope
- **Kiosk mode** (fullscreen Chromium on Pi 5, auto-connect, kiosk flags) — handled by **Epic 6**.
  The design stays kiosk-friendly (responsive, fullscreen-capable), but no kiosk-specific code here.
- Phoneme/viseme lip-sync (volume-based only).

## Decisions (from grilling)
- **Frontend**: vanilla JS as ES modules, no build step — a React SPA was considered and rejected (ADR-0011).
- **Animation**: Live2D via pixi-live2d-display + Cubism Core from CDN (ADR-0010).
- **Motion/emotion source**: the Agent Worker is authoritative — explicit motion events + LLM emotion
  tags (ADR-0009). Reverses earlier reliance on `lk.agent.state` alone.
- **Lip-sync**: added (reverses the original "no lip-sync"); volume-based, client-side (ADR-0008).
- **Layout**: avatar-centric (sidebar tools / large avatar / chat below) — deviates from the original
  diagram; "make it convenient and beautiful".
- **Sound**: via LiveKit WebRTC in the browser (not system speakers).

## Slices (sub-issues)
1. #26 — Frontend: vanilla ES modules + new layout
2. #27 — Live2D avatar + motion states (lk.agent.state)
3. #28 — Agent-authoritative motion events + LLM emotion tags
4. #29 — Volume-based lip-sync (ParamMouthOpenY)
5. #30 — Responsive desktop+mobile + polish

Dependency order: #26 → #27 → #28 → #29; #30 after #27.

## Technical notes
- pixi-live2d-display + PIXI.js + Cubism Core, all via CDN; model assets served statically.
- LiveKit client SDK: `livekit-client` (already integrated in the harness).
- Motion/emotion events ride the existing `voiceagent` LiveKit data-channel topic.
- Browser target: desktop + mobile Chromium/WebKit (Pi 5 kiosk Chromium covered in Epic 6).

## Dependencies
- Epic 1 (LiveKit SFU) — done
- Epic 4 (Agent Worker) — done

## Estimate
~10 чч (без киоска)
