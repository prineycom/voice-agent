# Volume-based lip-sync (ParamMouthOpenY) — implementation plan

**Task:** GitHub issue #29 (Epic 5 slice 4)
**Complexity:** simple
**Mode:** sub-agents
**Parallel:** true

## Design decisions

### DD-1: Drive `ParamMouthOpenY` from the `beforeModelUpdate` hook (not a bare RAF)

**Decision:** The avatar registers `model.internalModel.on('beforeModelUpdate', () => model.internalModel.coreModel.setParameterValueById('ParamMouthOpenY', mouthOpen))` once, in `avatar.init()` after the model loads, and exposes `setMouthOpen(v)` to update the shared `mouthOpen` value. The lip-sync module computes the value and calls `setMouthOpen`.
**Rationale:** Verified against `pixi-live2d-display@0.4.0` (cubism4 bundle): the per-frame order is motion → physics/pose → `beforeModelUpdate` → `model.update()`. Writing the param in `beforeModelUpdate` makes it the LAST writer before the frame commits, so the idle/TapBody motion's mouth keyframes can't overwrite it. An absolute `setParameterValueById` (not additive `addParameterValueById`) hard-overrides the motion. A plain `requestAnimationFrame` calling the setter races the library ticker → flicker/no movement (the documented failure mode). Base 0.4.0 has no `speak()`/`SoundManager` (that's a fork, URL-based) so the manual-parameter path is required for a live WebRTC track.
**Rationale (placement):** Keeping the Live2D-specific call inside `avatar.js` (which owns `model`) and exposing a model-agnostic `setMouthOpen(0..1)` keeps `lipsync.js` free of Live2D internals — same separation as slice 2/3.
**Alternative:** `addParameterValueById` — rejected (sums with motion → double/clip). Bare RAF setter — rejected (races, flickers).

### DD-2: Analyse the agent's WebRTC track with the vu.js AnalyserNode pattern

**Decision:** A new `lipsync.js` module `createLipSync(avatar, { log })` → `{ start(mediaStreamTrack), stop() }` builds its own `AudioContext` + `AnalyserNode` on the agent's track (mirroring `vu.js`), runs a `requestAnimationFrame` loop that computes RMS from `getFloatTimeDomainData`, maps it to a 0..1 mouth target (gain ≈ 4, noise gate `< 0.05 → 0`, lerp smoothing ≈ 0.5), and calls `avatar.setMouthOpen(target)` each frame. The analyser connects to the source only — NOT to `ctx.destination` (the audio already plays via the LiveKit `<audio>` element; connecting would double it). `stop()` cancels the RAF, closes the context, and calls `avatar.setMouthOpen(0)` so the mouth closes.
**Rationale:** ADR-0008 — reuse the existing `AudioContext`/`AnalyserNode` plumbing on the agent track; analysing the played audio keeps the mouth in sync for free. RMS is small, so gain+gate+smoothing are needed for a convincing, jitter-free, fully-closing mouth.
**Alternative:** Phoneme/viseme — rejected by ADR-0008 (large scope, marginal gain). Reusing the single vu.js instance for both mic and agent — rejected; vu.js is bound to the bar element and mic semantics, and lip-sync needs floating-point RMS + the avatar, so a dedicated module is cleaner.

### DD-3: Source the agent track in room.js `TrackSubscribed`; lifecycle mirrors vu/ops

**Decision:** Pass the `lipsync` instance into `room.connect(opts)` like `vu`/`ops`/`transcript`. In the existing `TrackSubscribed` audio branch (`room.js:36-43`), after `opts.attachAudio(el)`, call `opts.lipsync.start(track.mediaStreamTrack)`. In `onDisconnected`, call `hooks.lipsync.stop()` (alongside `vu.stop()`).
**Rationale:** The agent track is already obtained there for `attach()`; `track.mediaStreamTrack` is the analyser input. Reusing the hook/lifecycle pattern keeps `room.js` DOM-free and consistent with slices 1–3.
**Alternative:** A separate `RoomEvent.TrackSubscribed` listener elsewhere — rejected; duplicates the subscription and splits the audio-track handling.

### DD-4: Pure `computeMouthTarget(rms)` helper, unit-tested

**Decision:** Extract the RMS→mouth mapping (gain, gate, clamp) as an exported pure function `computeMouthTarget(rms)` in `lipsync.js` and unit-test it in `lipsync.test.mjs` (zero-dep Node, like `motion.test.mjs`). Smoothing (stateful lerp) stays in the RAF loop.
**Rationale:** The only logic testable while the rest is visual/audio; matches the project's light Node-test approach. AC #4 ("returns to closed when silent") is directly assertable (`computeMouthTarget(0) === 0`, sub-gate values → 0).
**Alternative:** No test — weaker; the gate/clamp is exactly the bit worth pinning.

## Tasks

### Task 1: avatar.js — mouth-open driver via `beforeModelUpdate`

- **Files:** `infra/pi/web/static/js/avatar.js` (edit)
- **Depends on:** none
- **Scope:** S
- **What:** Add a `setMouthOpen(v)` method and register a `beforeModelUpdate` hook that writes `ParamMouthOpenY` each frame.
- **How:** Add an internal `let mouthOpen = 0;`. In `init()`, AFTER `model = await ...from(...)` and `addChild`, register `model.internalModel.on('beforeModelUpdate', () => { try { model.internalModel.coreModel.setParameterValueById('ParamMouthOpenY', mouthOpen); } catch (e) {} });` (wrap defensively; an unknown param id silently no-ops). Add `function setMouthOpen(v) { mouthOpen = Math.max(0, Math.min(1, Number(v) || 0)); }`. Reset `mouthOpen = 0` in `dispose()`/`teardown()`. Export `setMouthOpen` in the returned object. Do not touch motion/expression logic.
- **Context:** `infra/pi/web/static/js/avatar.js` (the `init()` model-load block, the returned object, `teardown`), DD-1 (hook placement + absolute set).
- **Verify:** `node --check infra/pi/web/static/js/avatar.js`.

### Task 2: lipsync.js — agent-track RMS → mouth

- **Files:** `infra/pi/web/static/js/lipsync.js` (create), `infra/pi/web/static/js/lipsync.test.mjs` (create)
- **Depends on:** none (consumes avatar's `setMouthOpen` interface from DD-1)
- **Scope:** S
- **What:** New module analysing the agent track and driving `avatar.setMouthOpen`, plus a unit test for the pure mapping.
- **How:** `export function computeMouthTarget(rms)`: `const t = Math.min(1, Math.max(0, rms * 4)); return t < 0.05 ? 0 : t;`. `export function createLipSync(avatar, { log } = {})` → `{ start, stop }`. `start(mediaStreamTrack)`: `try { audioCtx = new (window.AudioContext||window.webkitAudioContext)(); const src = audioCtx.createMediaStreamSource(new MediaStream([mediaStreamTrack])); const analyser = audioCtx.createAnalyser(); analyser.fftSize = 512; analyser.smoothingTimeConstant = 0.2; src.connect(analyser); /* NOT to destination */ const buf = new Float32Array(analyser.fftSize); let cur = 0; const tick = () => { analyser.getFloatTimeDomainData(buf); let sum=0; for (const v of buf) sum += v*v; const rms = Math.sqrt(sum/buf.length); const target = computeMouthTarget(rms); cur += (target - cur) * 0.5; avatar.setMouthOpen(cur); raf = requestAnimationFrame(tick); }; tick(); } catch (e) { if (log) log('lip-sync недоступен: ' + e.message); }`. `stop()`: cancel `raf`, close `audioCtx` (guarded), reset internal state, and `avatar.setMouthOpen(0)`. Keep `audioCtx`/`raf` as module-instance state (mirror `vu.js`). In `lipsync.test.mjs` (zero-dep, `node:assert/strict`): assert `computeMouthTarget(0) === 0`, a sub-gate value (e.g. `0.005`) → 0, a mid value (e.g. `0.1` → `0.4`), and clamp (e.g. `1` → 1); print a pass line + `process.exit(0)`.
- **Context:** `infra/pi/web/static/js/vu.js` (the AnalyserNode/AudioContext/RAF pattern to mirror), DD-2, DD-4, `motion.test.mjs` (test style).
- **Verify:** `node --check infra/pi/web/static/js/lipsync.js && node --check infra/pi/web/static/js/lipsync.test.mjs && node infra/pi/web/static/js/lipsync.test.mjs`.

### Task 3: Wire lip-sync into room.js + main.js

- **Files:** `infra/pi/web/static/js/room.js` (edit), `infra/pi/web/static/js/main.js` (edit)
- **Depends on:** Task 1, Task 2
- **Scope:** S
- **What:** Start lip-sync on the agent's audio track and stop it on disconnect.
- **How:** In `room.js` `TrackSubscribed` audio branch (after `opts.attachAudio(el)`): `if (opts.lipsync) opts.lipsync.start(track.mediaStreamTrack);`. In `onDisconnected` (near `hooks.vu.stop()`): `if (hooks.lipsync) hooks.lipsync.stop();`. In `main.js`: `import { createLipSync } from './lipsync.js';`; after the avatar is created, `const lipsync = createLipSync(avatar, { log: logger.log });`; add `lipsync` to the `hooks` object passed to `room.connect(...)`. No other wiring.
- **Context:** `room.js:36-43` (TrackSubscribed), `room.js:75-88` (onDisconnected), `main.js` (avatar/ops construction + hooks object), Task 1/2 interfaces.
- **Verify:** `node --check` on room.js + main.js; `grep -n "lipsync" infra/pi/web/static/js/{room,main}.js` shows start + stop + construction.

### Task 4: Validation

- **Files:** —
- **Depends on:** all
- **Scope:** S
- **What:** Full syntax + unit validation of the changed surface.
- **How:** node --check everything; run the lip-sync + motion tests; confirm no stray issues.
- **Context:** —
- **Verify:** `for f in infra/pi/web/static/js/*.js; do node --check "$f"; done` — all green; `node infra/pi/web/static/js/lipsync.test.mjs` and `node infra/pi/web/static/js/motion.test.mjs` — pass. Live visual check (mouth moves during the agent's spoken greeting — note: the greeting is fixed TTS and plays WITHOUT the LLM, so lip-sync is testable on the Pi even while the LLM quota is exhausted) is a manual step in the report.

## Execution

- **Mode:** sub-agents
- **Parallel:** true
- **Reasoning:** Tasks 1 (avatar.js) and 2 (new lipsync.js + test) touch disjoint files and run together against the agreed `setMouthOpen(0..1)` interface; Task 3 wires room.js+main.js and depends on both; Task 4 validates.
- **Order:**
  Group 1 (parallel): Task 1, Task 2
  ─── barrier ───
  Group 2 (sequential): Task 3
  ─── barrier ───
  Group 3 (sequential): Task 4

## Verification

From issue #29 acceptance criteria:

- The agent's WebRTC audio track is analysed (RMS/peak) per animation frame in the browser.
- `ParamMouthOpenY` is driven from that volume so the avatar's mouth moves while the agent speaks.
- Mouth movement is visibly in sync with the spoken audio (manual check).
- Mouth returns to closed/neutral when the agent is silent (`computeMouthTarget(0)===0` + `stop()` resets to 0).
- No changes to the Desktop TTS service; lip-sync coexists with the `speaking` motion + expressions (independent param).

## Materials

- ADR-0008 `docs/adr/0008-client-volume-lipsync.md`.
- `infra/pi/web/static/js/vu.js` — the AnalyserNode/AudioContext/RAF pattern to mirror.
- pixi-live2d-display@0.4.0 (verified): `model.internalModel.coreModel.setParameterValueById('ParamMouthOpenY', v)` written inside `model.internalModel.on('beforeModelUpdate', …)`; analyser connected to source only (not destination); RMS×gain + gate + lerp.
- **Note:** the spoken greeting is fixed TTS (no LLM), so lip-sync can be verified live on the Pi even with the LLM quota exhausted.
