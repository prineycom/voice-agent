# Frontend: vanilla ES modules + new layout — implementation plan

**Task:** GitHub issue #26 (Epic 5 slice 1)
**Complexity:** medium
**Mode:** sub-agents
**Parallel:** true

## Design decisions

### DD-1: Static directory layout

**Decision:** Keep `infra/pi/web/index.html` as the served shell at `/`. Add a sibling
`infra/pi/web/static/` tree: `static/css/styles.css` and `static/js/*.js`. The browser loads
`<link rel="stylesheet" href="static/css/styles.css">` and `<script type="module" src="static/js/main.js">`.
Future Live2D model assets go under `static/models/` (created in slice 2, not here).
**Rationale:** One static root the stdlib `server.py` can serve verbatim; matches "served as static files by the stdlib `http.server`" (ADR-0011). `index.html` stays the entry the existing `voice-agent-web.service` `WorkingDirectory` expects (`infra/pi/web/deploy/voice-agent-web.service`).
**Alternative:** Move `index.html` into `static/` and serve the whole dir as a webroot — rejected; needlessly changes the `/` → `index.html` mapping and the deploy unit's assumptions.

### DD-2: LiveKit SDK stays a CDN UMD global

**Decision:** Keep loading `livekit-client` via the CDN `<script>` tag (UMD → `window.LivekitClient`). ES modules read `window.LivekitClient` rather than importing an ESM build.
**Rationale:** No build step (ADR-0011); the harness already depends on the UMD global (`index.html:112-115`). Switching to an ESM CDN import is an unrelated change with its own failure modes.
**Alternative:** `import { Room } from 'https://cdn…/livekit-client.esm.mjs'` — rejected for this slice; no benefit, adds risk.

### DD-3: Module seams + interface (no behavior change in extraction)

**Decision:** Split the inline script into native ES modules under `static/js/`, each a self-contained
controller created from its DOM element(s) so later slices can reuse them without touching transport:

- `log.js` — `export function createLog(logEl)` → `{ log(msg) }` (timestamped, newest-first; also `console.log`).
- `agent-state.js` — `export function createAgentState(badgeEl)` → `{ set(state), watch(participant) }` (the `setAgent` label/colour map + `lk.agent.state` attribute watcher).
- `vu.js` — `export function createVuMeter(barEl)` → `{ start(mediaStreamTrack), stop(), setMuted(bool) }` (the `AudioContext`/`AnalyserNode` RAF loop).
- `transcript.js` — `export function createTranscript(containerEl, { isLocal, onLatency })` → `{ wire(room), reset() }` (the `renderLine` + `wireTranscriptions` dual-source logic and latency stamping).
- `ops.js` — `export function createOps(opsEl, toolfeedEl, { log })` → `{ wire(room), startTick(), stopTick(), reset() }` (the `voiceagent` data-channel `renderOps`/`drawOps`/`addToolEvent`/`resetOpsUI` + 1s live tick).
- `room.js` — `export function createRoomController({ onLog, onConn, onError })` → `{ connect(els, hooks), disconnect() }`: token fetch, `Room` creation, event wiring, mic enable, mute toggle. Composes the controllers above via injected hooks rather than importing DOM.
- `main.js` — entry: grabs DOM elements, instantiates every controller, wires Connect/Disconnect/Mute buttons. The only module that touches `document.getElementById`.

**Rationale:** Mirrors the existing functional groupings in `index.html` (transcript ~158-221, ops ~223-290, vu ~305-323, agent-state ~149-303, connect ~325-403) so extraction is mechanical and reviewable; factory-from-element keeps modules framework-free and reusable by avatar/motion/lip-sync slices.
**Alternative:** One big `app.js` — rejected; defeats the "reusable modules" acceptance criterion. Per-feature classes — rejected; factories match the existing plain-function style.

### DD-4: New layout — sidebar / avatar placeholder / transcript

**Decision:** Replace the `grid-template-columns: 280px 1fr` (aside | transcript) with: left **sidebar**
(ops, tools, log, latency, mic/VU, status) + a right column that **stacks** a large central **avatar
placeholder** over a scrolling **transcript** chat below it. Header (connect/mute/badges) stays on top.
The avatar placeholder is an empty `#avatar` box with a "Live2D — slice 2" hint; no PIXI/Live2D here.
**Rationale:** Matches the grilled layout decision (`.yoke/context.md`, epic body lines 11/20) and leaves a mounting point for slice 2 without coupling.
**Alternative:** Avatar beside transcript (3 columns) — rejected; epic specifies avatar **above** transcript with sidebar on the left.

### DD-5: server.py serves the static tree

**Decision:** Extend `server.py` `do_GET` to serve files under `infra/pi/web/static/` for any `/static/...`
path, with a small extension→MIME map (`.js`→`text/javascript`, `.css`→`text/css`, plus existing text/html),
and path-traversal containment (resolve under the static root, reject escapes). Keep `/`, `/index.html`,
`/token`, `/healthz` exactly as they are.
**Rationale:** Acceptance criterion "serves a static directory … keeping `/token` and `/healthz`"; stdlib-only, no FastAPI (CLAUDE.md).
**Alternative:** Swap to `http.server.SimpleHTTPRequestHandler` subclass / `partial(..., directory=)` — rejected; would fight the existing custom `/token` routing and the `.env` cred loading already in `Handler`.

## Tasks

### Task 1: server.py — serve the `static/` tree

- **Files:** `infra/pi/web/server.py:104-147` (edit `do_GET`), plus module-level path const near `INDEX_HTML` (`server.py:33-34`)
- **Depends on:** none
- **Scope:** S
- **What:** Add static-file serving for `/static/...` requests rooted at `infra/pi/web/static/`, keeping `/`, `/index.html`, `/token`, `/healthz` unchanged.
- **How:** Add `STATIC_ROOT = HERE / "static"`. In `do_GET`, after the existing routes, if `route` starts with `/static/`: resolve the requested path under `STATIC_ROOT` with `(STATIC_ROOT / rel).resolve()`, verify it's inside `STATIC_ROOT` (`is_relative_to`) and `is_file()` — else `404`. Pick content type from a small dict by suffix (`.js`→`text/javascript; charset=utf-8`, `.css`→`text/css; charset=utf-8`, `.html`→`text/html; charset=utf-8`, `.json`→`application/json; charset=utf-8`, default `application/octet-stream`). Read bytes and `_send(200, …)`. Keep `Cache-Control: no-store` (already in `_send`). Do not touch token/healthz logic.
- **Context:** `infra/pi/web/server.py:33-34` (`INDEX_HTML`), `server.py:92-150` (`Handler`, `_send`, `do_GET`).
- **Verify:** `python3 -m py_compile infra/pi/web/server.py` — green; manual: `curl -s localhost:PORT/static/js/main.js` returns the file with `Content-Type: text/javascript` and `/healthz` still returns `ok`.

### Task 2: Extract CSS + reusable transport modules (no behavior change)

- **Files:** `infra/pi/web/static/css/styles.css` (create), `infra/pi/web/static/js/log.js` (create), `infra/pi/web/static/js/agent-state.js` (create), `infra/pi/web/static/js/vu.js` (create), `infra/pi/web/static/js/transcript.js` (create), `infra/pi/web/static/js/ops.js` (create), `infra/pi/web/static/js/room.js` (create)
- **Depends on:** none
- **Scope:** L
- **What:** Move the inline `<style>` into `static/css/styles.css` verbatim, and extract the inline JS into the six reusable ES modules defined in DD-3, preserving behavior exactly. Do **not** edit `index.html` in this task (Task 3 owns the shell).
- **How:** Copy the `<style>` block (`index.html:7-71`) into `styles.css` unchanged (layout-specific rules are re-tuned in Task 3, not here). For each module use native `export function create…` factories per DD-3, lifting the corresponding logic verbatim: `log` from `index.html:143-147`; `agent-state` from `setAgent` (149-154) + `watchAgentState` (296-303); `vu` from `startVU` (305-323) reading `muted` via an internal flag set by `setMuted`; `transcript` from `renderLine` (158-186) + `wireTranscriptions` (192-221), taking injected `isLocal` and `onLatency(ms)`; `ops` from `wireDataChannel`/`renderOps`/`drawOps`/`addToolEvent`/`resetOpsUI` (223-294) plus `esc` (292-294) and the 1s `setInterval(drawOps)` as `startTick`/`stopTick`; `room` from `connect`/`onDisconnected`/`disconnect` (325-403) composing the others through hooks (no `document` access inside `room.js`). Modules read `window.LivekitClient` for `Room`/`RoomEvent`/`Track` (DD-2). Keep all user-facing strings (Russian) and behavior identical.
- **Context:** `infra/pi/web/index.html:7-71` (CSS), `index.html:113-415` (the full inline script), DD-3 above for the export interface.
- **Verify:** `node --check` on each `static/js/*.js` — green (ESM syntax valid).

### Task 3: New layout shell + main.js wire-up

- **Files:** `infra/pi/web/index.html` (rewrite body + head links), `infra/pi/web/static/js/main.js` (create), `infra/pi/web/static/css/styles.css:layout-rules` (edit layout section only)
- **Depends on:** Task 2
- **Scope:** M
- **What:** Replace the inline `<style>`/`<script>` with `<link href="static/css/styles.css">` and `<script type="module" src="static/js/main.js">`; restructure the markup to the DD-4 layout (sidebar | avatar-placeholder-over-transcript); write `main.js` to grab DOM elements, instantiate every controller from Task 2, and wire the Connect/Disconnect/Mute buttons. Preserve every existing feature and the header badges.
- **How:** In `index.html`: keep `<header>` (badges + mute/connect). Change `<main>` to `grid-template-columns: <sidebar> 1fr`; the right column is a flex/grid `column` with `#avatar` (large, flex-grow, placeholder text "Live2D — slice 2") above `#transcript` (scrolling, fixed/limited height). Move the ops/tools/log/latency/mic panels into the left `<aside>` sidebar (reuse existing `.panel` markup + IDs `#ops`, `#toolfeed`, `#log`, `#latency`, `#vuBar`, `#micLabel`). Keep all element IDs the modules expect. Add the matching layout rules to `styles.css` (avatar box, the stacked right column, sidebar width). In `main.js`: `import` the six controllers, resolve all elements via `getElementById`, build `isLocal`/`onLatency` for transcript, pass `log` into ops, and replicate the original button wiring (`connect`/`disconnect` swap on the Connect button, mute toggle) using `room.connect(...)`/`room.disconnect()`. No transport logic lives in `main.js`.
- **Context:** `infra/pi/web/index.html:73-110` (current markup), `index.html:117-130` (element map), `index.html:405-414` (button wiring), Task 2 module interfaces (DD-3), DD-4 layout.
- **Verify:** `node --check infra/pi/web/static/js/main.js` — green; manual smoke: load `/`, Connect, confirm two-way audio, streaming transcript, tool/ops feed, mute, VU meter, and both connection + agent-state badges work; avatar placeholder visible above transcript, panels in the left sidebar.

### Task 4: Validation

- **Files:** —
- **Depends on:** all
- **Scope:** S
- **What:** Run full validation for the changed surface.
- **How:** Syntax-check every changed file; sanity-check the page wiring.
- **Context:** —
- **Verify:** `python3 -m py_compile infra/pi/web/server.py` && `for f in infra/pi/web/static/js/*.js; do node --check "$f"; done` — all green; no remaining inline `<style>`/`<script>` logic in `index.html` (only `<link>`, the CDN `<script>`, and the module `<script>`).

## Execution

- **Mode:** sub-agents
- **Parallel:** true
- **Reasoning:** Task 1 (`server.py`) and Task 2 (new `static/` files) touch disjoint files and can run together; Task 3 rewrites `index.html`/adds `main.js` and depends on Task 2's module interface; Task 4 validates everything.
- **Order:**
  Group 1 (parallel): Task 1, Task 2
  ─── barrier ───
  Group 2 (sequential): Task 3
  ─── barrier ───
  Group 3 (sequential): Task 4

## Verification

From issue #26 acceptance criteria:

- `index.html` is split into vanilla ES modules; transport logic (LiveKit/token, transcript, tool+ops feed, mic/VU, status) lives in reusable modules with no build step.
- `server.py` serves a static directory (JS modules + assets), keeping `/token` and `/healthz`.
- New layout: left sidebar (tools/ops), central avatar placeholder, scrolling transcript chat below.
- All prior features still work: connect, two-way audio, streaming transcript, tool/ops feed, mute, VU meter, connection + agent-state indicators.
- No regression in token minting or the `voiceagent` data-channel rendering.

## Materials

- ADR-0011 `docs/adr/0011-frontend-vanilla-es-modules.md` — vanilla ES modules, no build step.
- `.yoke/context.md` — glossary (Sidebar, Avatar, Test harness, UI topic `voiceagent`).
- `infra/pi/web/deploy/voice-agent-web.service` — deploy unit (WorkingDirectory `infra/pi/web`, runs `server.py`).
