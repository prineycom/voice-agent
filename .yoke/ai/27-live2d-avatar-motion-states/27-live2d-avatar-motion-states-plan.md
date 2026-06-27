# Live2D avatar + motion states (lk.agent.state) — implementation plan

**Task:** GitHub issue #27 (Epic 5 slice 2)
**Complexity:** medium
**Mode:** sub-agents
**Parallel:** true

## Design decisions

### DD-1: Live2D stack + exact CDN versions (verified live)

**Decision:** Load three CDN `<script>` tags in `index.html`, in this exact order, before the
`main.js` module:
1. `https://cdn.jsdelivr.net/npm/pixi.js@6.5.10/dist/browser/pixi.min.js` → global `PIXI`
2. `https://cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js` → global `Live2DCubismCore`
3. `https://cdn.jsdelivr.net/npm/pixi-live2d-display@0.4.0/dist/cubism4.min.js` → `PIXI.live2d`

**Rationale:** `pixi-live2d-display@0.4.0` peer-depends on PIXI v6 (NOT v7/v8); `6.5.10` is the last
v6. Cubism Core must load before the plugin. Matches ADR-0010 (CDN, no build step). Cubism Core is
loaded from Live2D's official distribution (license-appropriate); only PIXI + plugin come from jsDelivr.
**Alternative:** PIXI v7 + `0.5.0-beta` — rejected (prerelease); official Cubism SDK for Web — rejected by ADR-0010 (build step).

### DD-2: Model = Haru (`haru_greeter_t03`), served locally under `static/models/haru/`

**Decision:** Ship the Haru Cubism 4 sample from the `pixi-live2d-display` test assets, downloaded
into `infra/pi/web/static/models/haru/` preserving subdirs (`expressions/`, `motion/`,
`haru_greeter_t03.2048/`). License: Live2D Free Material License + Sample Data Terms (free at this scale).
Strip the `Sound` keys from the local `*.model3.json` (they point at non-bundled shizuku sounds → 404s).
**Rationale:** Verified to ship `.exp3.json` (8 expressions) + motions, satisfies "free Cubism sample
with expressions served as a static asset". Served same-origin → no CORS.
**Alternative:** Cubism 2 Shizuku (more motion groups but legacy `index.min.js` bundle), or runtime CDN
load of the model — rejected (AC requires a served static asset; CORS/availability risk).

### DD-3: Motion-controller maps state → {motion group, expression}

**Decision:** `motion.js` exposes `createMotionController(avatar)` → `{ setState(state) }`. It holds a
config table mapping each `lk.agent.state` to a Haru **motion group** plus a distinguishing
**expression**, with `idle` as the default/fallback for `null`/unknown/`initializing`. Haru ships only
two motion groups (`Idle`, `Tap`), so visible distinction across all four states comes from pairing the
group with a distinct expression:

| state         | motion group | expression |
| ------------- | ------------ | ---------- |
| idle (default)| `Idle`       | (neutral / none) |
| listening     | `Idle`       | `f01`      |
| thinking      | `Idle`       | `f03`      |
| speaking      | `Tap`        | `f00`      |
| initializing / null | `Idle` | (neutral)  |

State→motion-group is the primary mapping (the AC abstraction); the expression is secondary polish so
each state is visibly distinct. Slice 3 swaps the *source* (authoritative motion events) by calling the
same `setState` — no avatar/motion code rewrite. The table is the only thing a richer model changes.
**Rationale:** Honors "motion-controller abstraction maps a state → Live2D motion group" AND "visibly
switches motion among the four states" given Haru's real group set (verified from its `model3.json`).
**Alternative:** Map every state to a unique motion group — impossible on Haru (only 2 groups); a model
with 4+ groups would inflate asset size for marginal benefit this slice.

### DD-4: `avatar.js` owns PIXI/model lifecycle; `motion.js` owns the state→group policy

**Decision:** `avatar.js` exposes `createAvatar(containerEl)` →
`{ init(), playMotion(group), setExpression(name), dispose() }` (async `init` mounts a `PIXI.Application`
into `#avatar`, loads the model via `PIXI.live2d.Live2DModel.from(...)`, centers + fits with a
`ResizeObserver`, calls `registerTicker`). `motion.js` consumes that avatar and translates states. The
avatar is created and `init()`-ed once at page load (not gated on connect), so it renders immediately;
`motion.setState(null)` shows idle until the agent connects.
**Rationale:** Separation matches the existing factory-module style (slice 1) and the AC's explicit
"motion controller abstraction"; keeps PIXI/WebGL concerns out of the state policy.
**Alternative:** One combined `avatar.js` — rejected; the AC calls out the controller abstraction and
slice 3 reuses it.

### DD-5: Drive motion from the existing `onAgentState` path

**Decision:** In `main.js`, instantiate the avatar + motion controller, `await avatar.init()`, and
extend the `onAgentState` hook so the SAME `lk.agent.state` update that sets the badge also calls
`motion.setState(state)`. No change to `room.js` (its `onAgentState(state)` / `onAgentState(null)` on
disconnect already fire on the right events).
**Rationale:** `room.js:47` already calls `opts.onAgentState(st)` on every attribute change and
`opts.onAgentState(null)` on disconnect (verified). Reusing it keeps `room.js` untouched and DOM-free.
**Alternative:** A new dedicated avatar hook in `room.js` — rejected; redundant, the state path already exists.

### DD-6: server.py — add `.png` (and binary) content types

**Decision:** Add `.png` → `image/png` to `_STATIC_CONTENT_TYPES`. `.moc3`/`.exp3.json`/`.motion3.json`
already resolve (`.json` → `application/json`; `.moc3` → octet-stream default, which the loader reads as
an ArrayBuffer regardless).
**Rationale:** Correct image MIME for textures; everything else already works via the slice-1 static route.
**Alternative:** Leave PNG as octet-stream — works for `<img>`-based texture load but is incorrect; cheap to fix.

## Tasks

### Task 1: Download the Haru model into `static/models/haru/`

- **Files:** `infra/pi/web/static/models/haru/**` (create — model3.json, moc3, physics3, pose3, 2 textures, 5 motions, 8 expressions)
- **Depends on:** none
- **Scope:** S
- **What:** Fetch all Haru asset files from the verified jsDelivr base into `infra/pi/web/static/models/haru/`, preserving subdirectories, then strip `Sound` references from the local `*.model3.json`.
- **How:** Run the verified download loop (base `https://cdn.jsdelivr.net/gh/guansss/pixi-live2d-display@master/test/assets/haru`) creating `expressions/`, `motion/`, `haru_greeter_t03.2048/`. Files: `haru_greeter_t03.model3.json`, `haru_greeter_t03.moc3`, `haru_greeter_t03.physics3.json`, `haru_greeter_t03.pose3.json`, `haru_greeter_t03.2048/texture_00.png`, `haru_greeter_t03.2048/texture_01.png`, `motion/haru_g_idle.motion3.json`, `motion/haru_g_m05.motion3.json`, `motion/haru_g_m07.motion3.json`, `motion/haru_g_m14.motion3.json`, `motion/haru_g_m15.motion3.json`, `expressions/F0{1..8}.exp3.json`. After download, edit `haru_greeter_t03.model3.json` to remove any `Sound` keys under `FileReferences.Motions.*` entries (keep the motion `File` keys). Verify every file is non-empty and `model3.json` is valid JSON (`python3 -c "import json,...; json.load(...)"`). Add `infra/pi/web/static/models/` to git with `git add -f` if textures exceed the default ignore threshold (the orchestrator handles the commit).
- **Context:** the verified URL list in this plan; the model3.json `FileReferences` structure.
- **Verify:** all 20 files present and non-empty; `model3.json` parses as JSON and contains no `Sound` keys; `Motions` still has groups `Idle` and `Tap`.

### Task 2: server.py — add image/binary content types

- **Files:** `infra/pi/web/server.py:41-46` (the `_STATIC_CONTENT_TYPES` dict)
- **Depends on:** none
- **Scope:** S
- **What:** Add `.png` → `image/png; charset=...`? no — `image/png` (binary, no charset). Optionally `.moc3` → `application/octet-stream` (explicit).
- **How:** Add `".png": "image/png"` to the `_STATIC_CONTENT_TYPES` dict. Leave the octet-stream default for `.moc3`. Do not touch routing or any other logic.
- **Context:** `infra/pi/web/server.py:41-46`, `:157-168` (static branch).
- **Verify:** `python3 -m py_compile infra/pi/web/server.py`; serving `/static/.../texture_00.png` returns `Content-Type: image/png`.

### Task 3: avatar.js + motion.js modules

- **Files:** `infra/pi/web/static/js/avatar.js` (create), `infra/pi/web/static/js/motion.js` (create)
- **Depends on:** none (code-only; runtime needs Task 1's model)
- **Scope:** M
- **What:** Implement the two ES modules per DD-3/DD-4: `avatar.js` mounts PIXI + loads the Haru model + fits/centers it responsively; `motion.js` maps `lk.agent.state` → motion group + expression.
- **How:** `avatar.js` `export function createAvatar(containerEl)` → `{ init, playMotion, setExpression, dispose, get ready() }`. `init()` (async): `PIXI.live2d.Live2DModel.registerTicker(PIXI.Ticker)`; create `new PIXI.Application({ resizeTo: containerEl, backgroundAlpha: 0, antialias: true, autoDensity: true, resolution: devicePixelRatio||1 })`; append `app.view` (the canvas) into `containerEl`; `const model = await PIXI.live2d.Live2DModel.from('static/models/haru/haru_greeter_t03.model3.json')`; `app.stage.addChild(model)`; `model.anchor.set(0.5,0.5)`; a `layout()` that scales by `Math.min(screen.w/model.internalModel.width, screen.h/model.internalModel.height)*0.9` and centers; observe `containerEl` with `ResizeObserver`. Guard against a missing global (`window.PIXI?.live2d`) and surface load errors via an injected `log`/`onError` (accept `createAvatar(containerEl, { log })`). `playMotion(group)` → `model.motion(group, undefined, PIXI.live2d.MotionPriority.FORCE)`; `setExpression(name)` → `model.expression(name)`; `dispose()` destroys the app + disconnects the observer. `motion.js` `export function createMotionController(avatar)` → `{ setState(state) }` holding the DD-3 table; `setState` resolves the state (lowercased; `null`/unknown/`initializing` → idle), and only re-triggers `playMotion`/`setExpression` when the resolved entry changes (debounce identical states). No DOM access in `motion.js`.
- **Context:** DD-3 table, DD-4 interface, the verified API sketch (`Live2DModel.from`, `model.motion(group,index?,priority?)`, `model.expression(name)`, `model.internalModel.width/height`), Haru groups `Idle`/`Tap`, expression Names `f00`–`f07`.
- **Verify:** `node --check infra/pi/web/static/js/avatar.js && node --check infra/pi/web/static/js/motion.js`.

### Task 4: Wire avatar into the shell (index.html + main.js + styles.css)

- **Files:** `infra/pi/web/index.html` (add 3 CDN scripts + canvas host), `infra/pi/web/static/js/main.js` (instantiate + hook), `infra/pi/web/static/css/styles.css:33-38` (avatar canvas sizing)
- **Depends on:** Task 1, Task 3
- **Scope:** M
- **What:** Load the Live2D CDN stack, mount the avatar in `#avatar`, and drive motion from `onAgentState`.
- **How:** In `index.html` `<head>`/before `main.js`: add the three DD-1 `<script>` tags in order, BEFORE the existing `<script type="module" src="static/js/main.js">` (the LiveKit UMD script stays). Keep `#avatar`; the avatar module appends its own `<canvas>` into it (remove the static `.avatar-hint` span, or leave it and let CSS/JS hide it once the model loads — prefer removing it from markup and showing a small "загрузка аватара…" fallback that JS clears on ready). In `main.js`: `import { createAvatar } from './avatar.js'` and `{ createMotionController } from './motion.js'`; after resolving `#avatar`, `const avatar = createAvatar(avatarEl, { log: logger.log })`; `const motion = createMotionController(avatar)`; `await avatar.init()` (wrap in try/catch → `logger.log` on failure, keep the page functional); change the `onAgentState` hook from `agentState.set` to `(state) => { agentState.set(state); motion.setState(state); }`; call `motion.setState(null)` once after init for the initial idle pose. In `styles.css` `#avatar`: ensure the injected canvas fills the box (`#avatar canvas { width:100%; height:100%; display:block; }`), keep the dashed border/background or drop it now that real content renders (keep `min-height:0`, the flex sizing, and `position` context if needed for the canvas).
- **Context:** `index.html:44-54` (#avatar + script tags), `main.js:24,46-73` (instantiation + hooks), `styles.css:33-38` (#avatar), Task 3 module interfaces.
- **Verify:** `node --check infra/pi/web/static/js/main.js`; `index.html` contains the 3 CDN scripts in correct order before the module script; manual: load `/`, the Haru avatar renders in the central area, fits without breaking the sidebar/transcript layout, and switches motion/expression as `lk.agent.state` moves through listening/thinking/speaking.

### Task 5: Validation

- **Files:** —
- **Depends on:** all
- **Scope:** S
- **What:** Full validation of the changed surface + a served-asset smoke test.
- **How:** Syntax-check all changed code; boot the server (agent venv) and curl the model assets to confirm they serve with sane status/MIME.
- **Context:** —
- **Verify:** `python3 -m py_compile infra/pi/web/server.py` && `for f in infra/pi/web/static/js/*.js; do node --check "$f"; done` — all green; with the server running: `GET /static/models/haru/haru_greeter_t03.model3.json` → 200 `application/json`, `GET /static/models/haru/haru_greeter_t03.moc3` → 200, `GET /static/models/haru/haru_greeter_t03.2048/texture_00.png` → 200 `image/png`; `index.html` references PIXI + Cubism Core + pixi-live2d-display + `main.js` in the right order.

## Execution

- **Mode:** sub-agents
- **Parallel:** true
- **Reasoning:** Tasks 1 (model assets), 2 (server MIME), 3 (avatar/motion modules) touch disjoint files and run together; Task 4 wires the shell and depends on the model + modules; Task 5 validates.
- **Order:**
  Group 1 (parallel): Task 1, Task 2, Task 3
  ─── barrier ───
  Group 2 (sequential): Task 4
  ─── barrier ───
  Group 3 (sequential): Task 5

## Verification

From issue #27 acceptance criteria:

- Avatar renders in the central area via pixi-live2d-display + Cubism Core from CDN, no build step.
- A free Cubism sample model with `.exp3.json` expressions is served as a static asset.
- A motion-controller abstraction maps a state → Live2D motion group.
- Avatar visibly switches motion among idle/listening/thinking/speaking driven by `lk.agent.state`.
- Avatar scales reasonably within the central area without breaking the transcript/sidebar layout.

## Materials

- ADR-0010 `docs/adr/0010-live2d-pixi-cubism-core.md` — pixi-live2d-display + Cubism Core, CDN, no build step.
- `.yoke/context.md` — glossary (Avatar, Motion state, Expression).
- Slice 1 (#26) modules `infra/pi/web/static/js/{room,main,agent-state}.js` — the `onAgentState` hook path.
- Verified motion groups: `Idle`, `Tap`; expressions `f00`–`f07`. Model license: Live2D Free Material + Sample Data Terms.
