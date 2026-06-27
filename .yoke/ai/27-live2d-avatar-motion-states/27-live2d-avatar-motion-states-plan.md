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

### DD-2: Model = Natori (official Live2D Cubism 4 sample), served locally under `static/models/natori/`

**Decision:** Ship the **Natori** sample from `Live2D/CubismWebSamples`, pinned to commit
`b032ce27e111a228138b9363be408ff24bc7eaa6`, downloaded into `infra/pi/web/static/models/natori/`
preserving subdirs (`exp/`, `motions/`, `Natori.2048/`). ~3.5 MB, single 2048 texture, **no `Sound`
references** (no 404s). License: Live2D Cubism Free Material / Sample Data Terms — free at this scale
(see DD-7 for the mandatory attribution).

**Rationale:** Verified the richest reliably-downloadable FREE Cubism 4 sample: **8 distinct motions**
(`Idle`×3, `TapBody`×5) + **11 expressions** — 4× the animation surface of baseline Haru — which is
what "visibly switches motion among the four states" actually needs. Pinned raw URLs (not a moving
`@master`). Served same-origin → no CORS.
**Alternative:** `haru_greeter_t03` (pixi-live2d-display's own test asset; 5 motions / 8 expressions) —
kept as the documented fallback if Natori ever hits a Cubism-Core version mismatch. Mao (8 motions /
8 expressions) — equivalent motion, fewer expressions.

**Verified fact (the reason there is no "4 motion groups" option):** every free official Cubism 4
sample uses the Live2D 2-group convention (`Idle` + `Tap`/`TapBody`). Models with 4+ named motion
groups are ripped commercial assets and are not legally usable. So the controller maps states to
specific `motion(group, index)` calls, not to four distinct group names.

### DD-3: Motion-controller maps state → {motion group, index, expression}

**Decision:** `motion.js` exposes `createMotionController(avatar)` → `{ setState(state) }`. It holds a
config table mapping each `lk.agent.state` to a Natori **motion** (group + index, played via
`model.motion(group, index, FORCE)`) plus a distinguishing **expression**, with `idle` as the
default/fallback for `null`/unknown/`initializing`:

| state               | motion (group / index) | expression |
| ------------------- | ---------------------- | ---------- |
| idle (default)      | `Idle` / 0             | `Normal`   |
| listening           | `Idle` / 1             | `Normal`   |
| thinking            | `TapBody` / 0          | `Blushing` |
| speaking            | `TapBody` / 2          | `Smile`    |
| initializing / null | `Idle` / 0             | `Normal`   |

State→motion is the primary mapping (the AC abstraction); the expression is secondary polish so each
state is visibly distinct. Slice 3 swaps the *source* (authoritative motion events) by calling the same
`setState` — no avatar/motion code rewrite. The table is the only thing a different model changes.
**Rationale:** Honors "motion-controller abstraction maps a state → Live2D motion" AND "visibly switches
motion among the four states" using Natori's real, verified motion set (`Idle`×3, `TapBody`×5) and
expression names (`Normal`, `Smile`, `Sad`, `Surprised`, `Angry`, `Blushing`, `exp_01`–`exp_05`).
**Alternative:** Map every state to a unique motion group — impossible on any free model (2-group convention).

### DD-4: `avatar.js` owns PIXI/model lifecycle; `motion.js` owns the state→motion policy

**Decision:** `avatar.js` exposes `createAvatar(containerEl, { log })` →
`{ init(), playMotion(group, index), setExpression(name), dispose(), get ready() }` (async `init`
mounts a `PIXI.Application` into `#avatar`, loads the model via `PIXI.live2d.Live2DModel.from(...)`,
centers + fits with a `ResizeObserver`, calls `registerTicker`). `motion.js` consumes that avatar and
translates states. The avatar is created and `init()`-ed once at page load (not gated on connect), so
it renders immediately; `motion.setState(null)` shows idle until the agent connects.
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

### DD-6: server.py — add `.png` content type

**Decision:** Add `.png` → `image/png` to `_STATIC_CONTENT_TYPES`. `.moc3`/`.exp3.json`/`.motion3.json`
already resolve (`.json` → `application/json`; `.moc3` → octet-stream default, which the loader reads as
an ArrayBuffer regardless).
**Rationale:** Correct image MIME for the texture; everything else already works via the slice-1 static route.
**Alternative:** Leave PNG as octet-stream — works for `<img>`-based texture load but is incorrect; cheap to fix.

### DD-7: Display the mandatory Live2D attribution

**Decision:** Render the required credit line *"This content uses sample data owned and copyrighted by
Live2D Inc."* in the UI — a small muted caption under/near the avatar (and it stays visible, not only
on hover). Do not alter the character design.
**Rationale:** Live2D Sample Data Terms make attribution mandatory and forbid design modification; this
is a license obligation, not optional polish. Applies to any Live2D sample model (Natori, Haru, …).
**Alternative:** Bury it only in a README — rejected; the terms expect it shown where the content is used.

## Tasks

### Task 1: Download the Natori model into `static/models/natori/`

- **Files:** `infra/pi/web/static/models/natori/**` (create — model3.json, moc3, physics3, pose3, cdi3, 1 texture, 8 motions, 11 expressions)
- **Depends on:** none
- **Scope:** S
- **What:** Fetch all Natori asset files from the pinned `Live2D/CubismWebSamples` commit into `infra/pi/web/static/models/natori/`, preserving subdirectories.
- **How:** Run the download loop with base
  `https://raw.githubusercontent.com/Live2D/CubismWebSamples/b032ce27e111a228138b9363be408ff24bc7eaa6/Samples/Resources/Natori`
  (jsDelivr mirror `https://cdn.jsdelivr.net/gh/Live2D/CubismWebSamples@b032ce27e111a228138b9363be408ff24bc7eaa6/Samples/Resources/Natori` is an equivalent fallback). Create `exp/`, `motions/`, `Natori.2048/`. Files: `Natori.model3.json`, `Natori.moc3`, `Natori.physics3.json`, `Natori.pose3.json`, `Natori.cdi3.json`, `Natori.2048/texture_00.png`, `motions/mtn_00..mtn_07.motion3.json` (8), `exp/{Angry,Blushing,Normal,Sad,Smile,Surprised}.exp3.json` + `exp/exp_01..exp_05.exp3.json` (11). Use `curl -fsSL` so a 404 fails loudly. After download, verify every file is non-empty and `Natori.model3.json` parses as JSON with `FileReferences.Motions` groups `Idle` and `TapBody`. The orchestrator commits with `git add -f infra/pi/web/static/models/natori` (the 2.5 MB texture exceeds the default binary-ignore threshold).
- **Context:** the pinned URL base + file list in this plan; the `model3.json` `FileReferences` structure.
- **Verify:** all ~22 files present and non-empty; `Natori.model3.json` valid JSON; `Motions` has `Idle` (3) and `TapBody` (5); `exp/` has 11 `.exp3.json`.

### Task 2: server.py — add image content type

- **Files:** `infra/pi/web/server.py:41-46` (the `_STATIC_CONTENT_TYPES` dict)
- **Depends on:** none
- **Scope:** S
- **What:** Add `.png` → `image/png` so textures serve with the correct MIME.
- **How:** Add `".png": "image/png"` to the `_STATIC_CONTENT_TYPES` dict. Leave the octet-stream default for `.moc3`. Do not touch routing or any other logic.
- **Context:** `infra/pi/web/server.py:41-46`, `:157-168` (static branch).
- **Verify:** `python3 -m py_compile infra/pi/web/server.py`; serving a `.png` returns `Content-Type: image/png`.

### Task 3: avatar.js + motion.js modules

- **Files:** `infra/pi/web/static/js/avatar.js` (create), `infra/pi/web/static/js/motion.js` (create)
- **Depends on:** none (code-only; runtime needs Task 1's model)
- **Scope:** M
- **What:** Implement the two ES modules per DD-3/DD-4: `avatar.js` mounts PIXI + loads the Natori model + fits/centers it responsively; `motion.js` maps `lk.agent.state` → motion (group+index) + expression.
- **How:** `avatar.js` `export function createAvatar(containerEl, { log })` → `{ init, playMotion, setExpression, dispose, get ready() }`. `init()` (async): if `!window.PIXI?.live2d` → log and return false; `PIXI.live2d.Live2DModel.registerTicker(PIXI.Ticker)`; create `new PIXI.Application({ resizeTo: containerEl, backgroundAlpha: 0, antialias: true, autoDensity: true, resolution: devicePixelRatio||1 })`; append `app.view` into `containerEl`; `const model = await PIXI.live2d.Live2DModel.from('static/models/natori/Natori.model3.json')`; `app.stage.addChild(model)`; `model.anchor.set(0.5,0.5)`; a `layout()` scaling by `Math.min(screen.w/model.internalModel.width, screen.h/model.internalModel.height)*0.9` and centering; observe `containerEl` with `ResizeObserver`; set `ready=true`. Wrap load in try/catch → `log` on failure, return false. `playMotion(group, index)` → `model.motion(group, index, PIXI.live2d.MotionPriority.FORCE)`; `setExpression(name)` → `model.expression(name)`; `dispose()` destroys the app + disconnects the observer. `motion.js` `export function createMotionController(avatar)` → `{ setState(state) }` holding the DD-3 table; `setState` resolves the state (lowercased; `null`/unknown/`initializing` → idle), debounces identical resolved states, and on change calls `avatar.playMotion(group, index)` + `avatar.setExpression(name)` (guarded by `avatar.ready`). No DOM access in `motion.js`.
- **Context:** DD-3 table, DD-4 interface, the verified API (`Live2DModel.from`, `model.motion(group,index,priority)`, `model.expression(name)`, `model.internalModel.width/height`), Natori groups `Idle`/`TapBody`, expression names (`Normal`,`Smile`,`Sad`,`Surprised`,`Angry`,`Blushing`,`exp_01..05`).
- **Verify:** `node --check infra/pi/web/static/js/avatar.js && node --check infra/pi/web/static/js/motion.js`.

### Task 4: Wire avatar into the shell (index.html + main.js + styles.css)

- **Files:** `infra/pi/web/index.html` (add 3 CDN scripts + canvas host + attribution), `infra/pi/web/static/js/main.js` (instantiate + hook), `infra/pi/web/static/css/styles.css:33-38` (avatar canvas + attribution caption)
- **Depends on:** Task 1, Task 3
- **Scope:** M
- **What:** Load the Live2D CDN stack, mount the avatar in `#avatar`, drive motion from `onAgentState`, and show the Live2D attribution (DD-7).
- **How:** In `index.html` before `main.js`: add the three DD-1 `<script>` tags in order, BEFORE `<script type="module" src="static/js/main.js">` (the LiveKit UMD script stays). Keep `#avatar`; the avatar appends its own `<canvas>` into it. Replace the static `.avatar-hint` with a small "загрузка аватара…" fallback that JS clears once `ready`, and add a persistent muted attribution caption `<small class="live2d-credit">This content uses sample data owned and copyrighted by Live2D Inc.</small>` (inside or just under `#avatar`). In `main.js`: `import { createAvatar } from './avatar.js'` and `{ createMotionController } from './motion.js'`; after resolving `#avatar`, `const avatar = createAvatar(avatarEl, { log: logger.log })`; `const motion = createMotionController(avatar)`; `await avatar.init()` in try/catch (log on failure, keep the page working); on success clear the loading fallback; change the `onAgentState` hook from `agentState.set` to `(state) => { agentState.set(state); motion.setState(state); }`; call `motion.setState(null)` once after init for the initial idle pose. In `styles.css`: `#avatar canvas { width:100%; height:100%; display:block; }`; keep `#avatar` flex sizing/`min-height:0`; style `.live2d-credit` as a tiny muted caption that doesn't break layout.
- **Context:** `index.html:44-54` (#avatar + script tags), `main.js:24,46-73` (instantiation + hooks), `styles.css:33-38` (#avatar), Task 3 module interfaces, DD-7.
- **Verify:** `node --check infra/pi/web/static/js/main.js`; `index.html` has the 3 CDN scripts in correct order before the module script and the attribution caption; manual: load `/`, the Natori avatar renders centrally, fits without breaking the sidebar/transcript layout, and switches motion+expression as `lk.agent.state` moves through listening/thinking/speaking.

### Task 5: Validation

- **Files:** —
- **Depends on:** all
- **Scope:** S
- **What:** Full validation of the changed surface + a served-asset smoke test.
- **How:** Syntax-check all changed code; boot the server (agent venv) and curl the model assets.
- **Context:** —
- **Verify:** `python3 -m py_compile infra/pi/web/server.py` && `for f in infra/pi/web/static/js/*.js; do node --check "$f"; done` — all green; with the server running: `GET /static/models/natori/Natori.model3.json` → 200 `application/json`, `GET /static/models/natori/Natori.moc3` → 200, `GET /static/models/natori/Natori.2048/texture_00.png` → 200 `image/png`; `index.html` references PIXI + Cubism Core + pixi-live2d-display + `main.js` in the right order; the Live2D attribution caption is present.

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
- A motion-controller abstraction maps a state → Live2D motion (group + index).
- Avatar visibly switches motion among idle/listening/thinking/speaking driven by `lk.agent.state`.
- Avatar scales reasonably within the central area without breaking the transcript/sidebar layout.
- (License) The mandatory Live2D attribution is displayed in the UI.

## Materials

- ADR-0010 `docs/adr/0010-live2d-pixi-cubism-core.md` — pixi-live2d-display + Cubism Core, CDN, no build step.
- `.yoke/context.md` — glossary (Avatar, Motion state, Expression).
- Slice 1 (#26) modules `infra/pi/web/static/js/{room,main,agent-state}.js` — the `onAgentState` hook path.
- Model: **Natori**, `Live2D/CubismWebSamples@b032ce27e111a228138b9363be408ff24bc7eaa6`. Verified motion
  groups `Idle` (3) / `TapBody` (5); 11 expressions. License: Live2D Free Material + Sample Data Terms
  (attribution mandatory, no design modification).
