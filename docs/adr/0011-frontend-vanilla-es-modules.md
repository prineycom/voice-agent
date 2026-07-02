---
tags:
  - voice-agent
  - adr
status: accepted
---

# Frontend Stays Vanilla ES Modules (rejected React SPA)

A React SPA rewrite was considered for Epic 5 and rejected. The frontend stays vanilla
JavaScript, refactored from the single `index.html` harness into ES modules, with no build
step — served as static files by the stdlib `http.server`. This holds the existing Epic 5
constraint ("no React/Vue overhead for Pi 5 browser") against an explicit proposal to change
it, so it is recorded to avoid re-litigating the choice later.

## Considered Options

- **Vanilla ES modules, no build step** ✅ — zero runtime overhead on the Pi 5 Chromium target,
  no bundler/toolchain, consistent with how the rest of the web tier is served.
- **React SPA (Vite)** ❌ — better DX/componentization, but adds a build step and runtime weight
  the project explicitly chose to avoid; the avatar stack (PIXI + WebGL) is already the heavy
  part of the page.
- **Preact** ❌ — lighter than React but still introduces a build step for marginal benefit at
  this UI's complexity.

## Consequences

- Shared transport logic (LiveKit, transcript, tool feed) is extracted into reusable ES modules
  consumed by the frontend; the dev harness page survives as a developer tool.
- No bundler enters the repo; the web server keeps serving plain static files.
