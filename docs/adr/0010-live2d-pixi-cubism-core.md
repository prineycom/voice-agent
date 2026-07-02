---
tags:
  - voice-agent
  - adr
status: accepted
---

# Live2D via pixi-live2d-display + Cubism Core (CDN, no build step)

The avatar is rendered with the community `pixi-live2d-display` wrapper on top of PIXI.js,
loaded from a CDN, with Live2D's proprietary Cubism Core runtime. This keeps the frontend
build-step-free (it is served as static files by the stdlib `http.server`, matching the
existing harness) while giving a high-level motion/expression API out of the box.

## Considered Options

- **pixi-live2d-display + Cubism Core (CDN)** ✅ — minimal code to load a model and play
  motions/expressions; no bundler. Requires shipping the proprietary Cubism Core blob (free
  under Live2D's license at this project's scale).
- **Official Cubism SDK for Web** ❌ — more control, but TypeScript framework with a build step
  and significant boilerplate; conflicts with the no-build-step constraint.

## Consequences

- A proprietary runtime blob lives in an otherwise open stack — a deliberate, license-bound
  dependency, not an oversight.
- The model is a free Cubism sample shipping `.exp3.json` expression files; the emotion enum is
  mapped to those expressions on the frontend.
- If the project's revenue ever exceeds Live2D's free-tier threshold, the Cubism Core license
  must be revisited.
