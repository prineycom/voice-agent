## Parent

[Epic 5] Web Frontend — Live2D + Transcript + Tool Viz — https://github.com/prineycom/voice-agent/issues/5

## What to build

Render the **Avatar** in the central area and drive its four **Motion states**
(idle/listening/thinking/speaking). Use `pixi-live2d-display` on top of PIXI.js plus Live2D's
Cubism Core, all loaded from a CDN with no build step (see ADR-0010). Ship a free Cubism sample
model that includes `.exp3.json` expression files (e.g. Haru/Hiyori) so the later emotion slice has
expressions to play.

In this slice the motion source is the built-in `lk.agent.state` participant attribute the frontend
already reads (initializing/listening/thinking/speaking → mapped to the four motion states, with
`idle` as the default/disconnected fallback). A small **motion controller** abstraction maps a state
to a Live2D motion group, so slice 3 can later swap the source to authoritative agent **Motion
events** without rewriting the avatar code.

## Acceptance criteria

- [ ] Avatar renders in the central area via pixi-live2d-display + Cubism Core from CDN, no build step
- [ ] A free Cubism sample model with `.exp3.json` expressions is served as a static asset
- [ ] A motion-controller abstraction maps a state → Live2D motion group
- [ ] Avatar visibly switches motion among idle/listening/thinking/speaking driven by `lk.agent.state`
- [ ] Avatar scales reasonably within the central area without breaking the transcript/sidebar layout

## Blocked by

- #26 — Frontend: vanilla ES modules + new layout
