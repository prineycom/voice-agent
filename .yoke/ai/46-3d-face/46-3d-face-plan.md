# 3D browser face (RPM + three.js) driven by the A2F ARKit pipeline — implementation plan

**Task:** GitHub issue #46 (https://github.com/prineycom/voice-agent/issues/46) — ADR-0017 milestone 1
**Complexity:** medium-complex (new tech: three.js/WebGL, glTF, import maps — well-bounded thin slice)
**Mode:** sub-agents
**Parallel:** true

Frontend only, vanilla ESM, NO build step, self-hosted deps. New page on a separate URL
(`/face3d`) rendering a Ready Player Me `.glb` in self-hosted three.js, driving its morph
targets 1:1 from the existing A2F ARKit stream. Reuse the LiveKit audio + transcript + the A2F
consumer (`blendshapes.js`/`schedule.js`) unchanged — swap only the renderer. A2F-only lipsync
(no volume fallback). The Live2D page is untouched (additive).

## Grounding (verified against code)

- `server.py:178` `do_GET` is a plain string router; `server.py:182` serves `/`→`index.html`
  fresh from disk. `server.py:224` `/static/` serves any contained file (unknown suffix →
  `application/octet-stream`, so `.glb` works). No CSP anywhere. Adding `/face3d` = one branch.
- `blendshapes.js:43-47` sink `apply(f)` calls `facial.apply(f.arkit)` **then**
  `mouth.ingestA2FFrame(f)` with **no try/catch** — the retro lipsync-death shape. So the 3D
  `facial.apply` must swallow throws internally.
- `room.js:32` `opts.blendshapes.wire(room)`; `room.js:46,93` guard `if (opts.lipsync)` —
  omitting the lipsync hook needs zero room/server edits.
- Calibration files (`docs/research/data/39-a2f-calibration/run-joy.json` / `run-anger.json`)
  are already inject-shaped (`{frames:[{type,frame,t,arkit}]}`, 221 frames) but live under
  `docs/`, which the server does NOT serve.
- `arkit.py:13-29` `ARKIT_52` (PascalCase) + `arkit.py:34-39` Tongue-16. RPM = camelCase 1:1;
  Tongue* have no RPM target.
- Live2D sink to mirror: `avatar.js:138` `setMouthOpen`, `avatar.js:146` `setFaceParams`,
  applied last-writer `avatar.js:79`. `facial.js`/`mouth.js` split (mouth owns opening) —
  preserved in 3D.
- Tests: zero-dep Node ESM run directly (`node x.test.mjs`); no runner/package.json.

## Design decisions

### DD-1: New page is additive — copy the shell, swap only the renderer
**Decision:** `face3d.html` copies `index.html`'s header/aside/transcript/composer verbatim
(so reused hooks' DOM ids exist), replaces the `#avatar` mount, drops the Pixi/Cubism
`<script>` tags, keeps the LiveKit CDN tag (`room.js` needs `window.LivekitClient`), and swaps
`main.js` for `face3d-main.js`.
**Rationale:** `room.js`/`blendshapes.js`/`transcript.js`/`ops.js`/`vu.js`/`agent-state.js`/
`log.js` are DOM-free or id-driven → reused unchanged (ADR-0017 "swap only the renderer").
**Alternative:** Parameterize `index.html`/`main.js` with a renderer flag — rejected: violates
"do not touch the Live2D page."

### DD-2: `face3d-main.js` = `main.js` minus Live2D / lipsync / volume / motion
**Decision:** Drop `avatar/motion/lipsync/mouth/facial/accents/facedebug/arkit-map` imports; add
`createFaceRenderer` + `createFaceSinks`. No `forceVolume`, no `createLipSync`, **omit the
`lipsync` hook**. `motion` → no-op stub `{ setState(){}, applyMotionEvent(){} }`; `ops.onMotion`
→ no-op. Keep `blendshapes.audioStopped()` on `speaking→not` and `agentState.set`.
**Rationale:** A2F-only lipsync, static head for M1 (ADR-0017 §Sequencing 1).
**Alternative:** Keep motion wired to a 3D bone controller — deferred to milestone 2.

### DD-3: Preserve the facial/mouth split; resolve jawOpen double-drive by ownership
**Decision:** `facial.apply(arkit)` writes **all 52 morphs except `jawOpen`**; the 3D `mouth`
writes **only `jawOpen`** (`= clamp01(arkit.JawOpen)`, raw — NOT folded with `MouthClose`, since
RPM has its own `mouthClose` morph written once by facial). Each of the 52 written exactly once
per frame; the 16 Tongue* skipped.
**Rationale:** Mirrors the existing split (`arkit-map.js` excludes `ParamMouthOpenY`; `mouth.js:37`
owns opening) and makes the guard criterion literally testable (mouth's jaw write survives a
facial throw). `a2fMouthOpen`'s `JawOpen*(1-MouthClose)` is a Live2D single-param hack, dropped
for RPM's native two-morph mouth.
**Alternative:** facial writes all 52, mouth = lifecycle-only no-op. Rejected as primary ("mouth"
becomes vacuous, guard test loses meaning) — kept as the safe fallback if the jaw split is fiddly.

### DD-4: The 3D `facial.apply` is try/catch-guarded; the guard lives in the sink
**Decision:** `facial.apply` wraps map-build + `renderer.applyMorphs` in try/catch (+ optional
throttled `log`), so a morph/WebGL throw returns normally and `blendshapes.js:46` proceeds to
`mouth.ingestA2FFrame`. `mouth.ingestA2FFrame` likewise guarded.
**Rationale:** `blendshapes.js` is reused unchanged and has no try/catch; the only place to fix
the retro bug is inside the sink.
**Alternative:** Add try/catch to `blendshapes.js` — rejected: mutates a reused, tested module.

### DD-5: Two pure, GL-free modules carry all testable logic
**Decision:** (a) `arkit-rpm-map.js`: `arkitNameToRpm(name)=name[0].toLowerCase()+name.slice(1)`,
`arkitToRpmMorphs(arkit,{excludeJaw})` → `{camelName:value}`, exported `RPM_UNMAPPED` (16 Tongue*).
(b) `morph-apply.js`: `applyMorphInfluences(meshes, morphMap)` over an array of
`{morphTargetDictionary, morphTargetInfluences}` meshes (plain JS in tests). The renderer gathers
the mesh array once at load and passes it every frame.
**Rationale:** Coverage seam; matches `arkit-map.test.mjs`/`blendshapes.test.mjs` idiom; no WebGL
context needed.
**Alternative:** Do mapping inside the renderer — rejected: untestable.

### DD-6: Multi-mesh application — gather once, apply every frame
**Decision:** On load, `scene.traverse` collects every object with `morphTargetDictionary` +
`morphTargetInfluences`; `applyMorphs(map)` sets `influences[dict[name]]=v` on **every** mesh
whose dict has the key (Wolf3D_Head + Wolf3D_Teeth for jaw/mouth, EyeLeft/EyeRight for eyes).
**Rationale:** teeth/eyes desync if only the head mesh is driven.
**Alternative:** Drive only the head mesh — rejected (visible desync).

### DD-7: Self-host three.js via an import map; pin one release
**Decision:** `static/js/vendor/three.module.js` + `static/js/vendor/jsm/loaders/GLTFLoader.js` +
`static/js/vendor/jsm/utils/BufferGeometryUtils.js` (GLTFLoader's relative import). Import map in
`face3d.html` before the module script: `"three"→/static/js/vendor/three.module.js`,
`"three/addons/"→/static/js/vendor/jsm/`. Pin one stable release (e.g. r0.169.0).
**Rationale:** GLTFLoader imports the bare specifier `three`; ADR-0011/0017 mandate self-hosting,
no CDN for the new stack.
**Alternative:** CDN import map — rejected (ADR mandate + offline Pi).

### DD-8: Static head/gaze via camera framing + configurable asset path; blink is free
**Decision:** M1 sets a fixed camera framed on the head and a neutral pose (bones untouched).
`EyeBlink*` morphs arrive in the stream and animate via the normal apply path, so blink comes
free. The `.glb` path is a single `const` (asset or placeholder = one-line swap).
**Rationale:** ADR-0017 §Sequencing 1 (static head; idle+override are milestone 2).
**Alternative:** Procedural idle now — deferred.

### DD-9: Offline demo replayer to satisfy AC "demoed with run-joy/run-anger"
**Decision:** `face3d-demo.js` (gated by `?demo=joy|anger`) fetches `/static/demo/run-<label>.json`,
replays each frame via `window.__a2fInject(frame)` scheduled at `frame.t·1000`, then injects
`{done:true}`. Requires copying the two calibration JSONs into `static/demo/`.
**Rationale:** Makes AC#3 reproducible without a live agent, via the exact production consumer
path (`blendshapes.js:101`).
**Alternative:** Manual console injection — kept as fallback.

## Tasks

### Task 1: Vendor three.js + import-map assets
- **Files:** `static/js/vendor/three.module.js`, `static/js/vendor/jsm/loaders/GLTFLoader.js`,
  `static/js/vendor/jsm/utils/BufferGeometryUtils.js` (create)
- **Depends on:** none
- **Scope:** S/M
- **What:** Copy a pinned three release's ESM build + GLTFLoader + its one jsm dependency.
- **How:** Preserve the `jsm/` tree so GLTFLoader's `../utils/BufferGeometryUtils.js` resolves;
  both jsm files import bare `three` (satisfied by the import map, DD-7).
- **Context:** `server.py:224` serves them as `text/javascript`.
- **Verify:** files present; (T11) page loads with no 404/import errors.

### Task 2: Obtain + commit the RPM `.glb` (prerequisite; possible human/BLOCKED step)
- **Files:** `static/models/rpm/avatar.glb` (create)
- **Depends on:** none
- **Scope:** S
- **What:** An avatar `.glb` with ARKit-52 morph targets, no Draco compression.
- **How:** RPM hosted service reportedly down (2026) — if unfetchable, commit a placeholder
  `.glb` carrying ARKit-named morph targets; the renderer is asset-agnostic (DD-8).
- **Context:** served as `application/octet-stream`, which GLTFLoader accepts.
- **Verify:** (T11) model loads; `morphTargetDictionary` contains camelCase ARKit names.

### Task 3: Pure `arkit-rpm-map.js` + test (first de-risk)
- **Files:** `static/js/arkit-rpm-map.js`, `static/js/arkit-rpm-map.test.mjs` (create)
- **Depends on:** none
- **Scope:** S/M
- **What:** `arkitNameToRpm`, `arkitToRpmMorphs(arkit,{excludeJaw})`, `RPM_UNMAPPED` (16 Tongue*).
- **How:** mechanical first-char-lowercase; `excludeJaw` drops `JawOpen`; unknown/tongue skipped.
- **Context:** `arkit.py:13-39` name source; mirror `arkit-map.test.mjs` idiom.
- **Verify:** `node static/js/arkit-rpm-map.test.mjs` — green.

### Task 4: Pure `morph-apply.js` + test
- **Files:** `static/js/morph-apply.js`, `static/js/morph-apply.test.mjs` (create)
- **Depends on:** none
- **Scope:** S/M
- **What:** `applyMorphInfluences(meshes, morphMap)` over an array of
  `{morphTargetDictionary, morphTargetInfluences}`; sets influence on every mesh with the key;
  ignores missing keys.
- **How:** plain-JS fake meshes in tests (DD-5/DD-6).
- **Context:** models RPM head+teeth+eyes multi-mesh case.
- **Verify:** `node static/js/morph-apply.test.mjs` — head+teeth both get jaw; eyes untouched by a
  mouth key; unknown key no-ops.

### Task 5: `face3d-renderer.js` (three.js scene/model/loop)
- **Files:** `static/js/face3d-renderer.js` (create)
- **Depends on:** Task 1, Task 2, Task 4
- **Scope:** M
- **What:** `createFaceRenderer(container,{log})` → `init()` (load `.glb`, mount canvas into
  `#avatar`, gather `morphMeshes`, frame camera on head, start render loop), `applyMorphs(map)`
  (delegates to `applyMorphInfluences(morphMeshes, map)`), `dispose()`, `ready`.
- **How:** GLTFLoader via the import map; neutral static pose (DD-8); asset path a single `const`.
- **Context:** mirrors `avatar.js` lifecycle (`avatar.js:38 init`, `main.js:65 init().then`,
  `main.js:74 dispose`).
- **Verify:** (T11) visual load + `applyMorphs` moves the mesh.

### Task 6: `face3d-sinks.js` (guarded facial + jaw mouth) + contract test
- **Files:** `static/js/face3d-sinks.js`, `static/js/face3d-sinks.test.mjs` (create)
- **Depends on:** Task 3, Task 4
- **Scope:** M
- **What:** `createFaceSinks(renderer,{log})` → `facial.apply(arkit)`
  (`arkitToRpmMorphs(arkit,{excludeJaw:true})` → `renderer.applyMorphs`, **try/catch swallow**) +
  `facial.release()` (apply neutral/zeros); `mouth.beginA2FStream()` /
  `mouth.ingestA2FFrame(f)` (`renderer.applyMorphs({jawOpen: clamp01(f.arkit.JawOpen)})`, guarded)
  / `mouth.endA2FStream()`.
- **How:** `renderer` injected as `{applyMorphs}` so tests pass a recording/throwing fake (no GL).
- **Context:** contract from `blendshapes.js:39-52`; template `blendshapes.test.mjs`.
- **Verify:** `node static/js/face3d-sinks.test.mjs` — (a) facial excludes `jawOpen`, mouth writes
  it, no key double-written; (b) a throwing `applyMorphs` inside `facial.apply` does NOT
  propagate; (c) release zeroes.

### Task 7: `face3d.html` shell + `face3d-main.js` wiring
- **Files:** `static/js/face3d-main.js`, `infra/pi/web/face3d.html` (create)
- **Depends on:** Task 1, Task 5, Task 6
- **Scope:** M
- **What:** `face3d.html` = `index.html` layout minus Pixi/Cubism tags, plus the import map (DD-7)
  and `<script type=module src=static/js/face3d-main.js>`; `face3d-main.js` = `main.js` minus
  Live2D/lipsync/volume/motion (DD-2), `blendshapes = createBlendshapes({facial, mouth, log})`,
  hooks object omitting `lipsync`.
- **How:** reuse `styles.css` unchanged; keep the LiveKit CDN tag.
- **Context:** `main.js:19-61` wiring, `main.js:95-130` hooks, `room.js` hook contract.
- **Verify:** (T11) Connect joins the room and animates from the live stream.

### Task 8: `server.py` `/face3d` route
- **Files:** `infra/pi/web/server.py` (edit)
- **Depends on:** Task 7
- **Scope:** S
- **What:** add `FACE3D_HTML = HERE / "face3d.html"` and `if route == "/face3d":` mirroring
  `server.py:182-191`.
- **How:** one branch, no framework. `/static/` and `.glb`/`.js` need no server change.
- **Verify:** `curl -s localhost:8080/face3d | head` returns the new HTML; `/` still returns
  `index.html`.

### Task 9: Offline demo replayer + fixtures
- **Files:** `static/js/face3d-demo.js`, `static/demo/run-joy.json`, `static/demo/run-anger.json`
  (create)
- **Depends on:** Task 7, Task 8
- **Scope:** S/M
- **What:** on `?demo=joy|anger`, fetch `/static/demo/run-<label>.json`, schedule
  `window.__a2fInject(frame)` at `frame.t·1000`, then `{done:true}` (DD-9).
- **How:** copy the two `docs/research/data/39-a2f-calibration/*.json` into `static/demo/`.
- **Context:** injector at `blendshapes.js:101`; frames already inject-shaped.
- **Verify:** (T11) `?demo=joy` visibly animates mouth+expression.

### Task 10: Mapping-table documentation
- **Files:** `docs/research/46-arkit-rpm-morph-map.md` (create)
- **Depends on:** Task 3
- **Scope:** S
- **What:** 52-row PascalCase→camelCase table, jaw-ownership note (DD-3), 16 Tongue* listed as
  unmapped/skipped.
- **How:** generated from `arkit-rpm-map.js` as source of truth.
- **Context:** AC "mapping verified + documented; unmapped names listed."
- **Verify:** table has 52 mapped + 16 unmapped rows.

### Task 11: Validation (depends on all)
- **Files:** — (read-only verification)
- **Depends on:** all
- **Scope:** M
- **What/Verify:** (1) `node` each new `*.test.mjs` passes; existing `arkit-map.test.mjs` /
  `blendshapes.test.mjs` still pass; (2) `/face3d` loads, three.js + `.glb` render with no console
  404/import errors; (3) `?demo=joy` and `?demo=anger` visibly animate mouth+expression 1:1;
  (4) force `renderer.applyMorphs` to throw inside `facial.apply` → mouth still moves (guard
  proven); (5) live path: Connect joins the room, plays agent audio, face animates from the
  `voiceagent` stream; (6) `/` (Live2D) unchanged — `git status` shows
  `index.html`/`main.js`/`avatar.js`/`facial.js`/`mouth.js`/`arkit-map.js` untouched.

## Execution

- **Mode:** sub-agents
- **Parallel:** true
- **Reasoning:** files are disjoint (only `server.py` is an existing-file edit, T8 alone);
  parallelism is bounded only by the DAG. The renderer↔sinks interface
  (`applyMorphs({camelName:value})`) is small and explicit, so Track A (renderer) and Track B
  (sinks) run concurrently.
- **Order:**
  Group 1 (parallel): Task 1, Task 2, Task 3, Task 4
  ─── barrier ───
  Group 2 (parallel): Task 5, Task 6, Task 10
  ─── barrier ───
  Group 3: Task 7 → Task 8 → Task 9
  ─── barrier ───
  Group 4: Task 11 (Validation)

## Verification

From issue #46 acceptance criteria:

- New route serves the 3D page at its own URL; the Live2D page is untouched and still served.
- RPM avatar loads/renders via self-hosted three.js (no build step, no external CDN for the new
  stack).
- `window.__a2fInject` frames visibly animate the 3D face (mouth + expression morphs) 1:1 —
  demoed with `run-joy.json` / `run-anger.json`.
- ARKit-52 → RPM morph-name mapping verified + documented (a mapping table); unmapped names listed.
- Lipsync is A2F-only (no volume analyser); a throw in the face renderer cannot stop the mouth
  updates.
- Live path: page joins the LiveKit room, plays agent audio, face animates from the live A2F
  data-channel stream in sync.

## Materials

- `docs/adr/0017-3d-face-arkit-pipeline.md` — the accepted decision, milestone-1 scope, the "no
  volume fallback" and "guard the apply" mandates.
- `docs/retro-a2f-live2d-3d-pivot.md` — measured evidence, the lipsync-death mechanism, the
  52→12 ceiling rationale.
- Reuse unchanged: `infra/pi/web/static/js/blendshapes.js`, `schedule.js`, `room.js`,
  `transcript.js`, `ops.js`, `vu.js`, `agent-state.js`, `log.js`. Route: `infra/pi/web/server.py`.
- Contract seam: `blendshapes.js:39-52`. ARKit names: `infra/desktop/a2f/arkit.py:13-39`.
- Reference only (not a dependency): TalkingHead.js (met4citizen).

## Resolved decision — RPM `.glb` sourcing (Task 2)

**Milestone 1 uses a PLACEHOLDER `.glb`** (user decision). Task 2 commits an open/test glTF that
carries ARKit-52-named morph targets (camelCase); the renderer is asset-agnostic (DD-8), so the
real RPM avatar is a later one-line asset-path swap. Task 2 is therefore NOT a blocker — no task
depends on a licensed avatar. The placeholder MUST expose the ARKit morph-target names (else the
1:1 mapping has nothing to drive); if a ready placeholder with ARKit morphs cannot be found,
generate a minimal glTF with the ARKit-52 morph targets on a simple head mesh. Real-avatar
appearance is deferred to a follow-up (product choice).
