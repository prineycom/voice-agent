# Code Review: 27-live2d-avatar-motion-states

## Summary

### Context and goal

Slice 2 of Epic 5 mounts a Live2D avatar (Natori) in the `#avatar` stage area of the vanilla-JS,
no-build frontend and drives four motion states (idle/listening/thinking/speaking) off the existing
`lk.agent.state` path. Stack is CDN-loaded: PIXI 6.5.10 + Live2D Cubism Core + pixi-live2d-display@0.4.0,
plus the Natori model assets, a `.png` MIME entry in `server.py`, and CSS for the canvas/attribution.

### Key code areas for review

1. **`static/js/avatar.js`** — PIXI app + Cubism model lifecycle (`init`/`playMotion`/`setExpression`/`dispose`), responsive fit, failure teardown.
2. **`static/js/motion.js`** — pure state→motion/expression table + debounce (now unit-tested).
3. **`static/js/main.js`** — wiring of `avatar.init()` and `motion.setState` into `onAgentState`; teardown on unload.
4. **`index.html`** — CDN script order, `#avatar` host, Live2D attribution.
5. **`static/css/styles.css`** — canvas fill, hint/credit positioning.
6. **`server.py:46`** — `.png` → `image/png`.

### Complex decisions

1. **State → `motion(group, index)` + expression** (`motion.js`) — Natori has only 2 motion groups, so
   the 4 states are distinguished by group+index+expression; indices verified in range (`Idle` 0-2,
   `TapBody` 0-4).
2. **Avatar fully isolated from transport** (`main.js`) — a failed `avatar.init()` cannot break
   LiveKit/transcript/ops; `playMotion`/`setExpression` guard on `ready`.
3. **CDN script order** (`index.html`) — classic scripts (pixi → cubismcore → plugin) before the deferred module.

### Questions for the reviewer

1. The persistent stale-state seed (issue 3) now re-applies the last agent state after async load — confirm that matches desired UX.
2. `dispose()` is wired on `beforeunload` only; is any mid-session teardown needed (e.g. for a future avatar swap)?

### Risks and impact

- Main prior risk (WebGL/renderer leak on model-load failure) is fixed. No security impact: the `.png`
  MIME entry reuses the existing traversal-guarded `/static/` handler.
- Visual render remains unverified headless (Pi headless Chromium crashes) — manual check on the real display still required.

### Tests and manual checks

**Auto-tests:** `static/js/motion.test.mjs` — 12 assertions on resolve/debounce/table (zero-dependency Node ESM, passes).

**Manual scenarios:**

1. Load `/` → Natori renders centrally, fits without breaking sidebar/transcript.
2. Connect → avatar switches motion+expression across listening/thinking/speaking.
3. Disconnect → returns to idle.
4. Reconnect mid-`speaking` → avatar reflects current state after model load (issue 3 fix).

### Out of scope

- The 5 intentional design decisions (Natori choice, 2 motion groups, CDN Cubism Core, headless render, attribution string) and the binary model assets / `.yoke` markdown.

## Commits

| Hash      | Description |
| --------- | ----------- |
| `6eca34a` | docs: add implementation plan |
| `626b39c` | docs: switch model to Natori in plan |
| `57d6260` | feat: add Natori Live2D model assets |
| `50aa678` | feat: serve PNG textures with image/png MIME |
| `9139f95` | feat: add avatar and motion-controller modules |
| `fe85b96` | feat: mount Live2D avatar and drive motion from agent state |
| `54c16c7` | docs: add execution report |
| `667b973` | fix: fix 6 review issues |

## Changed Files

| File                                       | +/-     | Description |
| ------------------------------------------ | ------- | ----------- |
| `static/js/avatar.js`                      | +modified | Failure teardown, double-init guard, `dispose`=`teardown` (dedup). |
| `static/js/main.js`                        | +modified | Track `lastAgentState`; seed avatar with it post-load; `dispose()` on `beforeunload`. |
| `static/js/motion.js`                      | created | State→motion/expression policy. |
| `static/js/motion.test.mjs`                | created | 12-assertion unit test (resolve/debounce/table). |
| `index.html`                               | modified | 3 ordered CDN scripts, avatar host, attribution. |
| `static/css/styles.css`                    | modified | Canvas `display:block` (dropped redundant 100% sizing); hint/credit positioning. |
| `server.py`                                | +1      | `.png` → `image/png`. |
| `static/models/natori/**` (25 files)       | created | Natori Cubism 4 model assets. |

## Issues Found

| Severity  | Score | Category | File:line            | Description |
| --------- | ----- | -------- | -------------------- | ----------- |
| Important | 58    | bugs     | `avatar.js:34-39`    | Model-load failure left a live PIXI/WebGL renderer + orphan canvas (no teardown). |
| Minor     | 40    | bugs     | `avatar.js:12,25`    | No double-init guard — a second `init()` would build a second app/canvas. |
| Minor     | 33    | bugs     | `main.js:37`         | `setState(null)` post-load discarded any agent state that arrived during loading. |
| Minor     | 22    | quality  | `styles.css:39`      | Redundant `#avatar canvas { width/height:100% }` fights PIXI's inline sizing. |
| Minor     | 20    | tests    | `motion.js`          | Pure resolve/debounce logic untested. |
| Minor     | 15    | quality  | `avatar.js:78-89`    | `dispose()` exported but never called. |

## Fixed Issues

| Issue                              | Commit    | Description |
| ---------------------------------- | --------- | ----------- |
| Renderer/canvas leak on load fail  | `667b973` | Added `teardown()` (disconnect RO, `app.destroy(true,...)`, remove canvas, null state); called in the load `catch`. |
| No double-init guard               | `667b973` | `if (app) return ready;` at the top of `init()`. |
| Stale idle after async load        | `667b973` | Track `lastAgentState` in `main.js`; seed `motion.setState(lastAgentState)` once ready. |
| Redundant canvas CSS               | `667b973` | Dropped `width/height:100%`, kept `display:block`. |
| `motion.js` untested               | `667b973` | Added `motion.test.mjs` (12 assertions, passing). |
| `dispose()` never called           | `667b973` | Wired on `beforeunload`; consolidated `dispose` onto `teardown` (removed duplication). |

## Skipped Issues

> All found issues were fixed.

## Recommendations

- Do a visual pass on the Pi's real (non-headless) kiosk Chromium: avatar render, fit-to-layout, and the
  motion/expression switch across listening/thinking/speaking — the only unverifiable bit headless.
- The 12-assertion `motion.test.mjs` is runnable via `node infra/pi/web/static/js/motion.test.mjs`; consider
  adding it to a CI/lint hook if a frontend test runner is introduced later.
- Slice 3 (#28) swaps the motion source by calling the same `motion.setState`; Natori's 11 expressions map onto the emotion enum.
