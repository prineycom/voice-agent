# Code Review: 26-frontend-vanilla-es-modules-new-layout

## Summary

### Context and goal

GitHub issue #26 (Epic 5, slice 1) refactors the single-file test harness `infra/pi/web/index.html`
into vanilla ES modules with no build step, adds a new layout (left sidebar / central avatar
placeholder / transcript below), and extends `server.py` with a `/static/...` route. LiveKit stays a
CDN UMD global (`window.LivekitClient`); the avatar is an empty placeholder for the later Live2D slice.
Behavior is preserved verbatim.

### Key code areas for review

1. **`infra/pi/web/server.py:do_GET()` (`/static/` branch)** — static serving + path-traversal containment + content-type map.
2. **`infra/pi/web/static/js/room.js:createRoomController()`** — DOM-free LiveKit controller; the hook contract it consumes from `connect(opts)`.
3. **`infra/pi/web/static/js/main.js`** — the only DOM-aware module; resolves element IDs and supplies the hook set.
4. **`transcript.js` / `ops.js` / `vu.js` / `agent-state.js` / `log.js`** — extracted transport logic vs the original inline script.
5. **`index.html` + `styles.css`** — new layout, element-ID parity, module load order.

### Complex decisions

1. **DOM-free `room.js` via an injected hook object** (`static/js/room.js:4`) — every UI side effect is delegated to hooks supplied by `main.js`. Trade-off: more hook plumbing in exchange for a transport layer reusable by the avatar/motion/lip-sync slices.
2. **`muted` single source of truth** (`static/js/room.js:7-9`) — `room.js` owns `muted`; `vu.js` keeps only a cache written exclusively via `vu.setMuted(...)`.
3. **Static serving on the existing custom handler** (`server.py`) — extends `do_GET` rather than swapping to `SimpleHTTPRequestHandler`, to keep the bespoke `/token` routing and `.env` cred loading.

### Questions for the reviewer

1. `#transcript { flex: 0 0 40% }` is a fixed split — intended for the kiosk aspect ratio, or should it be content-driven?
2. Static assets inherit `Cache-Control: no-store` — fine for a Pi-local harness; revisit if asset caching becomes desirable.

### Risks and impact

- Low. Module wiring is complete (every hook `room.js` uses is supplied); load order is correct (classic UMD script before the deferred `type=module`). The full live LiveKit round-trip was not exercised headless — covered at code level by reviewers + a server-serving smoke test.

### Tests and manual checks

**Auto-tests:** none in this slice (frontend harness; no JS test toolchain in the repo).

**Manual scenarios:**

1. Connect → two-way audio, streaming transcript, agent-state badge, latency render.
2. Mute → Unmute → button text + mic label flip; VU reflects muted.
3. Disconnect → reconnect → starts unmuted, VU and button label consistent (review fix #2).
4. Failed connect (bad token) → no orphaned 1s ops interval (review fix #3).
5. `/static/...` 200 with correct MIME; traversal → 404; `/healthz` + `/token` unchanged.

### Out of scope

- Live2D avatar rendering (slice #27), kiosk concerns (Epic 6), build tooling, ESM CDN import of LiveKit.

## Commits

| Hash      | Description |
| --------- | ----------- |
| `78944bf` | docs: add implementation plan |
| `0d03930` | feat: serve static asset tree from token server |
| `adf8ffc` | refactor: extract transport logic into reusable ES modules |
| `e75cc68` | feat: rebuild shell with sidebar, avatar placeholder, module wiring |
| `f781f27` | docs: add execution report |
| `34bab22` | fix: fix 5 review issues |

## Changed Files

| File                                   | +/-       | Description |
| -------------------------------------- | --------- | ----------- |
| `infra/pi/web/index.html`              | +10/-381  | Thin shell: `<link>` + module `<script>`; sidebar / avatar / transcript layout. |
| `infra/pi/web/server.py`               | +23       | `/static/...` serving with MIME map + traversal containment. |
| `infra/pi/web/static/css/styles.css`   | +70       | Extracted styles + new layout rules. |
| `infra/pi/web/static/js/room.js`       | +111      | DOM-free LiveKit controller (hooks). |
| `infra/pi/web/static/js/main.js`       | +84       | Entry point: DOM wiring + button handlers. |
| `infra/pi/web/static/js/ops.js`        | +92       | `voiceagent` ops + tool feed + 1s tick. |
| `infra/pi/web/static/js/transcript.js` | +86       | Dual-source transcript + latency. |
| `infra/pi/web/static/js/vu.js`         | +40       | AudioContext/RAF VU meter. |
| `infra/pi/web/static/js/agent-state.js`| +21       | `lk.agent.state` badge + watcher. |
| `infra/pi/web/static/js/log.js`        | +10       | Timestamped log panel. |

## Issues Found

| Severity | Score | Category    | File:line                  | Description |
| -------- | ----- | ----------- | -------------------------- | ----------- |
| Minor    | 40    | quality     | `static/js/room.js:7` + `vu.js:36` | `muted` duplicated across two modules, synced only via `toggleMute()`. |
| Minor    | 35    | bug         | `static/js/vu.js` / `room.js` | Mute → disconnect → reconnect left VU at 0% and button text stale (inherited from original). |
| Minor    | 30    | performance | `static/js/room.js:61-65`  | Failed `connect()` left the 1s `drawOps` interval running (inherited). |
| Minor    | 20    | quality     | `static/js/transcript.js:9`| `querySelector('.empty')` ran on every render instead of caching. |
| Minor    | 15    | style       | `infra/pi/web/index.html:45`| Avatar placeholder repeated "слайс 2" twice (copy/paste artifact). |

## Fixed Issues

| Issue                                   | Commit    | Description |
| --------------------------------------- | --------- | ----------- |
| `muted` dual source of truth            | `34bab22` | `room.js` made the authority; `vu.js` flag is a cache driven only via `setMuted`; documented. |
| Stale mute across reconnect             | `34bab22` | Disconnect path resets `muted=false`, `vu.setMuted(false)`, and a new `resetMuteUI()` hook restores the button label. |
| Orphaned ops interval on failed connect | `34bab22` | Catch block calls `ops.stopTick()`/`ops.reset()` and nulls the half-built room. |
| Repeated `.empty` DOM query             | `34bab22` | Closure `emptyHintRemoved` flag; queried/removed once, reset in `reset()`. |
| Duplicated avatar hint text             | `34bab22` | Collapsed to a single `Live2D-аватар появится в слайсе 2`. |

## Skipped Issues

> All found issues were fixed.

## Recommendations

- Manually exercise the live LiveKit lifecycle on the Pi (connect/mute/disconnect/reconnect, transcript dual-source, ops tick) before merge — headless smoke only covered static serving.
- Confirm the `#transcript { flex: 0 0 40% }` split looks right on both desktop and the 1080p kiosk before slice #30 (responsive) builds on this layout.
- Slice #27 reuses `room.js` hooks and the `#avatar` mount point; `static/models/` is reserved for the Cubism model.
