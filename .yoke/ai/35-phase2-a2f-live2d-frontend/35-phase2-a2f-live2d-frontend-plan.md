---
ticket: "#35"
slug: 35-phase2-a2f-live2d-frontend
update_docs: false
---

# Plan: Epic 8 Phase 2 — A2F blendshapes → Live2D frontend

**Mode:** sub-agents
**Parallel:** yes (group 1: T1–T4 disjoint files)
**Ticket:** #35 · **Epic:** #33 Phase 2 · **ADRs:** 0013 (mouth provider + sync), 0012 (loose-sync face, expression removal)

Consume A2F ARKit blendshape frames on the `voiceagent` DataChannel and drive the Live2D **Hiyori** rig: eyes, gaze, brows, eye-squint, mouth *form* (loose-sync face), **and** mouth *opening* through a pluggable provider (A2F `JawOpen` primary + the existing volume analyser as fallback/toggle). Ship the mouth-sync seam per the `/yoke:grill` outcome: apply-on-arrival + an in-browser cross-correlator **instrument** + an empty (`D=0`) delay-line behind a clean seam; the adaptive alignment loop is Phase 3. Delete the frontend `.exp3.json` emotion-tag path (ADR-0012); motion states unchanged. Build against a dev injector so Phase 2 is demoable without Phase 3.

---

## Design decisions (resolved via /yoke:grill 2026-07-05)

**DD-1 — Module layout.** New `mouth.js` controller owns the provider **selector** + `A2FLipSync` + **delay-line** + **cross-correlator** and is the *single* caller of `avatar.setMouthOpen()`. `lipsync.js` stays `VolumeLipSync` **verbatim** — it is reused unchanged by constructing it with `mouth.volumeSink` in place of `avatar` (both expose `setMouthOpen`). Face path is separate: a pure `arkit-map.js` + a thin `facial.js` controller. `avatar.js` stays a rig sink (gains one absolute face-param setter). *Rationale:* isolates the hard sync machinery and keeps ADR-0013's "never delete the volume path" literal — no edit to `lipsync.js`.

**DD-2 — Mouth-opening ≠ arkit-map.** `arkitToLive2D()` maps **face only** (eyes/gaze/brows/squint/mouth-form), NOT `ParamMouthOpenY`. Mouth opening is computed inside `A2FLipSync` from `JawOpen` (+ `MouthClose`/`MouthFunnel` shaping) so it rides the pluggable provider/delay/correlator path. *Alternative rejected:* mapping `jawOpen→ParamMouthOpenY` in `arkit-map` would bypass the provider selector.

**DD-3 — Sync = measure-first, no playout clock.** `VolumeLipSync` already pushes an AGC envelope of the *played* audio into its sink at ~60 fps; A2F gives the `JawOpen` envelope. `mouth.js` records both (timestamped rings) and cross-correlates → offset in ms. Alignment (Phase 3) is an adaptive delay of the A2F path tuned by that offset — **no browser playout clock**, retiring the ADR's one unproven risk. Threshold is perceptual & asymmetric (ITU-R BT.1359: mouth may lag ≤ ~125 ms, lead ≤ ~45 ms). *Phase 2 ships:* apply-on-arrival (`D=0`) + the correlator instrument (log) + the empty delay-line seam; `t` (PTS) carried. Adaptive loop enabling/tuning + persisting `D` across streams = Phase 3.

**DD-4 — Provider toggle.** URL param `?lipsync=volume` overrides a config-constant default (A2F primary). No build step, works in the kiosk, assertable in tests.

**DD-5 — Stream-active detection.** Active from the first `type:"blendshapes"` frame until `{"done":true}`, **plus a ~250 ms idle-timeout** safety net (a `done` dropped on the lossy channel, or a barge-in truncation, must not pin the face/mouth in A2F mode forever). On close: `facial.release()` (face → Live2D auto-blink/idle) and `mouth.endA2FStream()` (mouth → `VolumeLipSync`, which self-closes on silence).

**DD-6 — A2F mouth shaping.** `A2FLipSync`: `open = clamp(JawOpen*(1 - MouthClose) )`, with a light lerp (~0.5) to smooth 30 fps → 60 fps render. **No AGC** — `JawOpen` is already normalised 0..1 (unlike `VolumeLipSync`'s RMS). `MouthFunnel` reserved for future shaping; kept in the formula surface.

---

## Interface contracts (pin these — sub-agents are context-isolated)

```
// arkit-map.js  (pure, no DOM)
export function arkitToLive2D(arkit) → {
  ParamEyeLOpen, ParamEyeROpen, ParamEyeLSmile, ParamEyeRSmile,
  ParamEyeBallX, ParamEyeBallY,
  ParamBrowLY, ParamBrowRY, ParamBrowLAngle, ParamBrowRAngle,
  ParamMouthForm, ParamCheek
}   // NEVER ParamMouthOpenY. Missing arkit keys → treat as 0.

// avatar.js  (rig sink — additions)
setFaceParams(mapOrNull)   // stores {paramId:number} pinned absolutely each frame in
                           // beforeModelUpdate (alongside ParamMouthOpenY); null = release
                           // (write nothing → Live2D auto-blink/idle runs).

// mouth.js
export function createMouth(avatar, { log, forceVolume=false } = {}) → {
  volumeSink,              // { setMouthOpen(v) } — pass to createLipSync() in place of avatar
  beginA2FStream(),        // consumer calls on first frame of a stream
  ingestA2FFrame(frame),   // frame = { t, arkit } → compute mouth-open, feed delay-line + envelope
  endA2FStream(),          // consumer calls on done/idle → provider back to volume
}
export function a2fMouthOpen(arkit) → 0..1        // pure (DD-6), unit-tested
export function crossCorrelateOffset(a, b) → ms   // pure, unit-tested (recovers a known offset)

// facial.js
export function createFacial(avatar) → { apply(arkit), release() }
//   apply: avatar.setFaceParams(arkitToLive2D(arkit));  release: avatar.setFaceParams(null)

// blendshapes.js  (DataChannel consumer + dev injector)
export function createBlendshapes({ facial, mouth, log }) → { wire(room), inject(frame) }
//   wire: room.on(DataReceived) topic 'voiceagent'; evt.type==='blendshapes' → handleFrame; evt.done → endStream
//   handleFrame: first-frame → facial + mouth.beginA2FStream(); facial.apply(arkit); mouth.ingestA2FFrame(evt); reset 250ms idle timer
//   endStream: clear timer; facial.release(); mouth.endA2FStream()
//   also sets window.__a2fInject = inject
```

ARKit keys are **PascalCase** per `infra/desktop/a2f/arkit.py` (`JawOpen`, `EyeBlinkLeft`, `EyeLookInLeft`, `BrowInnerUp`, `MouthFunnel`, `CheekSquintLeft`, …) — NOT the lowercase of research §9.

---

## Tasks

### T1 — `arkit-map.js` pure mapping + unit test
- **Files:** `infra/pi/web/static/js/arkit-map.js` (new), `infra/pi/web/static/js/arkit-map.test.mjs` (new)
- **Depends on:** —
- **Scope:** pure function, no DOM/globals.
- **What:** `arkitToLive2D(arkit)` mapping the face coeffs → the Live2D param object in the contract, per research §9 with PascalCase keys.
- **How:** formulas (missing key ⇒ 0):
  - `ParamEyeLOpen = 1 - EyeBlinkLeft` (+ `EyeWideLeft*0.1`); `ParamEyeROpen` symmetric.
  - `ParamEyeLSmile = EyeSquintLeft`; `ParamEyeRSmile = EyeSquintRight`.
  - `ParamEyeBallX = ((EyeLookOutLeft - EyeLookInLeft) + (EyeLookInRight - EyeLookOutRight)) / 2`.
  - `ParamEyeBallY = ((EyeLookUpLeft+EyeLookUpRight) - (EyeLookDownLeft+EyeLookDownRight)) / 2`.
  - `ParamMouthForm = (MouthSmileLeft+MouthSmileRight)/2 - (MouthFrownLeft+MouthFrownRight)/2 - MouthPucker`, clamped −1..1.
  - Brows: `ParamBrowLY = BrowInnerUp + BrowOuterUpLeft - BrowDownLeft`; `ParamBrowRY` symmetric (Right); `ParamBrowLAngle = -BrowDownLeft`; `ParamBrowRAngle = -BrowDownRight`.
  - `ParamCheek = (CheekSquintLeft+CheekSquintRight)/2`.
  - Clamp eye-open to 0..1; gaze/form/angle to −1..1. Allocation-light (one literal returned).
- **Context:** this is the loose-sync face only (DD-2). Mouth opening lives in `mouth.js`.
- **Verify:** `node infra/pi/web/static/js/arkit-map.test.mjs` — assert a neutral all-0 frame → eyes open=1, everything else 0; a blink frame → eye-open 0; a look-left and a smile frame → expected signed values; clamping holds.

### T2 — `avatar.js` face-param sink
- **Files:** `infra/pi/web/static/js/avatar.js`
- **Depends on:** —
- **Scope:** extend the rig sink only; no mapping logic here.
- **What:** `setFaceParams(mapOrNull)` + apply the map absolutely each frame in the existing `beforeModelUpdate` hook.
- **How:** add `let faceParams = null;`. New setter stores the arg. In the hook, after the `ParamMouthOpenY` write, `if (faceParams) for (const id in faceParams) try { coreModel.setParameterValueById(id, faceParams[id]); } catch {}`. `null` ⇒ write nothing (releases to Live2D idle/auto-blink). Reset `faceParams=null` in `teardown()`. Export `setFaceParams` in the returned object.
- **Context:** mirror the existing absolute-write, unknown-id-no-ops pattern (`avatar.js:77-81`). Keep allocation-light (no per-frame object creation).
- **Verify:** no unit test (DOM/Cubism). Lint-clean; `setFaceParams` exported; hook writes guarded by try/catch.

### T3 — `mouth.js` controller + unit test
- **Files:** `infra/pi/web/static/js/mouth.js` (new), `infra/pi/web/static/js/mouth.test.mjs` (new)
- **Depends on:** —
- **Scope:** the mouth-opening machinery per DD-1/DD-3/DD-6; new file, interface pinned above.
- **What:** `createMouth(avatar, {log, forceVolume})`, pure `a2fMouthOpen(arkit)`, pure `crossCorrelateOffset(a,b)`.
- **How:**
  - **Provider selection:** `a2fActive = false`. Active provider = `a2fActive && !forceVolume ? 'a2f' : 'volume'`.
  - **volumeSink.setMouthOpen(v):** record `{t: perfNow(), v}` into a bounded volume-envelope ring; if active provider is `volume` → `avatar.setMouthOpen(v)`. (When A2F drives, still record for correlation but don't forward.)
  - **a2fMouthOpen(arkit):** `clamp01(JawOpen * (1 - (MouthClose||0)))` (DD-6). Pure/exported.
  - **ingestA2FFrame(frame):** `const open = a2fMouthOpen(frame.arkit)`; lerp an internal `cur` toward `open` by ~0.5; record `{t: perfNow(), pts: frame.t, v: open}` into an A2F ring; push through the **delay-line** (`delayMs = 0` ⇒ apply now): if active provider is `a2f` → `avatar.setMouthOpen(cur)`.
  - **Delay-line seam:** a tiny `applyA2F(value)` indirection with a `delayMs = 0` constant and a comment marking where Phase 3 buffers against `pts`. Pass-through now (no scheduler). Keep it real (function boundary), not just a comment.
  - **beginA2FStream():** `a2fActive = true`; reset `cur`/rings for the new utterance.
  - **endA2FStream():** `a2fActive = false`; run the correlator instrument over the overlapping window and `log` the offset in ms (guard tiny/empty windows); mouth naturally returns to volume on the next `volumeSink` tick.
  - **crossCorrelateOffset(a, b):** resample both timestamped envelopes to a common grid (e.g. 10 ms), find the lag maximising normalised cross-correlation, return signed ms (positive = A2F leads). Pure, allocation-light, no DOM. Use `performance.now()` via an injectable `now` (default `performance.now`) so tests are deterministic.
  - Keep rings bounded (~2 s) to avoid GC growth.
- **Context:** DD-1 (single caller of `avatar.setMouthOpen`), DD-3 (measure-first, `D=0` seam), DD-6 (shaping). Do NOT import or modify `lipsync.js`.
- **Verify:** `node infra/pi/web/static/js/mouth.test.mjs` — `a2fMouthOpen`: JawOpen 0→0, 1→1, `MouthClose` reduces open; `crossCorrelateOffset` recovers a known injected offset (e.g. build B as A shifted +40 ms → returns ≈ +40 within grid tolerance); provider selection: with `forceVolume` a2f frames never call `avatar.setMouthOpen` (use a stub avatar).

### T4 — Remove `.exp3.json` expression path + fix stale note
- **Files:** `infra/pi/web/static/js/motion.js`, `infra/pi/web/static/js/motion.test.mjs`, `infra/desktop/a2f/arkit.py`
- **Depends on:** —
- **Scope:** delete only the expression half of `applyMotionEvent`; keep motion states.
- **What:** stop mapping `evt.emotion → EMOTION_EXPR → avatar.setExpression`; keep the state→motion half unchanged (ADR-0012). Fix the stale ADR-0008 note in `arkit.py`.
- **How:** in `motion.js` remove the `EMOTION_EXPR`/`FALLBACK_EXPR` lookups and the `avatar.setExpression(...)` call inside `applyMotionEvent` (and in `setState` if it only served expressions — keep `playMotion`). Leave `setExpression` on `avatar.js` (harmless, still used by nothing). Update `motion.test.mjs`: drop assertions on `setExpression`, keep motion-state assertions. In `arkit.py`, replace the docstring sentence "the mouth-*open* coefficient (`JawOpen`) is intentionally NOT used …" with a note that `JawOpen` now drives client mouth opening via `A2FLipSync` (ADR-0013).
- **Context:** Hiyori already maps all expressions to `null` (`avatar-config.js:39`), so runtime behaviour is unchanged — this removes dead wiring.
- **Verify:** `node infra/pi/web/static/js/motion.test.mjs` passes with expression assertions removed; `applyMotionEvent` no longer references `EMOTION_EXPR`.

### T5 — `facial.js` controller
- **Files:** `infra/pi/web/static/js/facial.js` (new)
- **Depends on:** T1 (`arkitToLive2D`), T2 (`avatar.setFaceParams`)
- **Scope:** thin glue between the pure map and the rig sink.
- **What:** `createFacial(avatar)` → `{ apply(arkit), release() }`.
- **How:** `apply(arkit){ avatar.setFaceParams(arkitToLive2D(arkit)); }` `release(){ avatar.setFaceParams(null); }`. Import `arkitToLive2D` from `./arkit-map.js`.
- **Context:** stream-active pin/release is driven by the consumer (DD-5); this module just forwards.
- **Verify:** lint-clean; imports resolve; no DOM beyond the injected `avatar`.

### T6 — `blendshapes.js` DataChannel consumer + dev injector
- **Files:** `infra/pi/web/static/js/blendshapes.js` (new)
- **Depends on:** T3 (`mouth`), T5 (`facial`)
- **Scope:** transport + stream-boundary detection (DD-5); mirror `ops.js:14-22`.
- **What:** `createBlendshapes({ facial, mouth, log })` → `{ wire(room), inject(frame) }`, and `window.__a2fInject = inject`.
- **How:** `wire(room)`: `room.on(RoomEvent.DataReceived, (payload,_p,_k,topic)=>{ if(topic!=='voiceagent') return; parse; if(evt.type==='blendshapes') handleFrame(evt); else if(evt.done) endStream(); })`. `handleFrame(evt)`: if not `active` → `active=true; mouth.beginA2FStream();`; `facial.apply(evt.arkit); mouth.ingestA2FFrame(evt);` reset a 250 ms idle timer (`setTimeout(endStream)`). `endStream()`: clear timer; if `active` → `active=false; facial.release(); mouth.endA2FStream();`. `inject(frame)`: route through the same `handleFrame`/`endStream` (accept a `{done:true}` too) so the pipeline is demoable without Phase 3. No dedup (lossy). Allocation-light per frame.
- **Context:** second `DataReceived` listener alongside `ops.js`/`transcript.js`; LiveKit allows multiple listeners. Keep the same topic guard.
- **Verify:** manual (needs a room/injector). Lint-clean; `window.__a2fInject` set; topic guard present; idle timer cleared on every boundary.

### T7 — Wiring: `main.js` + `room.js`
- **Files:** `infra/pi/web/static/js/main.js`, `infra/pi/web/static/js/room.js`
- **Depends on:** T2, T3, T5, T6
- **Scope:** construct and connect the new modules; do not change behaviour of existing hooks beyond the mouth-sink redirect.
- **What:** build `mouth`, redirect `lipsync` into `mouth.volumeSink`, build `facial` + `blendshapes`, wire the consumer into the room.
- **How:**
  - `main.js`: import `createMouth` (`./mouth.js`), `createFacial` (`./facial.js`), `createBlendshapes` (`./blendshapes.js`). After `avatar`: `const forceVolume = new URLSearchParams(location.search).get('lipsync') === 'volume';` `const mouth = createMouth(avatar, { log: logger.log, forceVolume });` Change `const lipsync = createLipSync(mouth.volumeSink, { log: logger.log });` (was `avatar`). `const facial = createFacial(avatar);` `const blendshapes = createBlendshapes({ facial, mouth, log: logger.log });` Add `blendshapes` to the `hooks` object.
  - `room.js`: in `connect`, after `opts.ops.wire(room);` add `if (opts.blendshapes) opts.blendshapes.wire(room);`. In `onDisconnected`, after `hooks.lipsync.stop()`, add `if (hooks.blendshapes) hooks.blendshapes.endStream?.();` — expose `endStream` from `blendshapes` for teardown (add to its return in T6 if cheap; else skip the disconnect reset and rely on the idle timer). Keep `lipsync.start(track.mediaStreamTrack)` unchanged — it now feeds `mouth.volumeSink`.
- **Context:** `lipsync.js` is untouched (DD-1); the redirect is entirely at the construction site. `room.js` already destructures a fixed hook set — add `blendshapes` there.
- **Verify:** `node --check` on `main.js`, `room.js`; the app loads without console errors (manual); `?lipsync=volume` forces the volume provider.

### T8 — Validation
- **Depends on:** all
- **What:** run the JS test suite + syntax checks; confirm the pipeline via the injector.
- **How:** `for f in infra/pi/web/static/js/*.test.mjs; do node "$f"; done`; `node --check` each modified/new `.js`; `python -c "import ast; ast.parse(open('infra/desktop/a2f/arkit.py').read())"`.
- **Verify:** all `.test.mjs` pass; no syntax errors.

---

## Execution

- **Mode:** sub-agents · **Parallel:** yes.
- **Reasoning:** T1–T4 touch disjoint files (arkit-map*, avatar.js, mouth*, motion*/arkit.py) → one parallel wave. T5 needs T1+T2; T6 needs T3+T5; T7 needs T2/T3/T5/T6 → sequential tail. Interfaces are pinned above so isolated agents can't drift.
- **Order:**
  1. **Parallel group** [T1, T2, T3, T4] → barrier.
  2. **T5** (facial).
  3. **T6** (blendshapes consumer).
  4. **T7** (wiring).
  5. **T8** (validation).

---

## Verification (from #35 Definition of Done)

- A2F frames on `voiceagent` drive Hiyori's eyes, gaze, brows, eye-squint, mouth *form* (via `facial` + `arkit-map`).
- Mouth *opening* driven by A2F `JawOpen` (primary) with `VolumeLipSync` as working fallback + `?lipsync=volume` toggle; no active stream → volume mouth + neutral face.
- Mouth-sync: apply-on-arrival shipped; cross-correlator instrument logs offset and recovers a known offset in unit tests; empty `D=0` delay-line seam in place; `t` (PTS) carried.
- Eye/brow released to Live2D auto-blink when idle (stream end / idle-timeout); `.exp3.json` expression path removed from the frontend; motion states unchanged.
- `arkitToLive2D` + `a2fMouthOpen` + `crossCorrelateOffset` unit-tested; `window.__a2fInject` demonstrates the pipeline without Phase 3.
- Out of scope (Phase 3): agent forwarding frames; adaptive delay loop enable/tune; persisting `D`; barge-in decay from the real agent; live latency measurement.
