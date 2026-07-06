# Epic 8 Phase 4: A2F blendshape playback scheduler — implementation plan

**Task:** https://github.com/prineycom/voice-agent/issues/38
**Complexity:** medium
**Mode:** sub-agents
**Parallel:** true

## Design decisions

### DD-1: Anchor source for the playback clock

**Decision:** First-frame-of-burst arrival: `anchorWall = now() − frame.t·1000`.
**Rationale:** `room.js:37-49` never stores the LiveKit `<audio>` element or its `currentTime` — element-time anchoring needs new plumbing through room.js. `frame.t` is authoritative reply-relative seconds (`tts_plugin.py:421`, `engine.py:85`), so anchoring at `now − t·1000` self-places the current frame at the present moment and schedules the rest of the burst forward at real time. The face trails the voice by the constant A2F compute+transport latency captured in the anchor — a bounded constant lag, explicitly acceptable per the issue and ADR-0013:35-37.
**Alternative:** Audio element `currentTime` (no access today, needs room.js change); audio-onset via the lipsync.js analyser (its rAF only runs in volume mode — the opposite of when the scheduler runs).

### DD-2: Lag policy

**Decision:** Fixed-lag lock with per-tick catch-up, no skip. `scheduled(f) = anchorWall + f.t·1000 + LAG_MS`; drain applies every frame with `scheduled(f) ≤ now()` in `t`-order. `LAG_MS = 100` default (≈3 frames @30fps), documented as the ADR-0013 tunable seam measured by the existing cross-correlator (`mouth.js:135-142`).
**Rationale:** With the anchor fixed at first-frame time, a normal tick has only 1–2 eligible frames. The while-loop only catches up after a rAF stall (backgrounded tab); applying all eligible in `t`-order is visually equivalent (only the last paints) and keeps `mouth.js` smoothing/cross-correlator semantics intact.
**Alternative:** Drop-to-latest (loses mouth detail, complicates the instrument); pure catch-up with no lag (re-introduces jitter sensitivity).

### DD-3: Scheduler placement

**Decision:** NEW pure module `schedule.js`, wired inside `blendshapes.js`.
**Rationale:** The scheduler must gate face + mouth atomically at the same tick with the same `arkit` object (two apply paths at `blendshapes.js:36-37` must move together). `mouth.js:105` `delayMs` only delays the mouth, not `facial.apply` — folding timing there desyncs face from mouth. Keeping the wiring inside `blendshapes.js` (single dispatch point, `:54-57`) leaves `main.js:60` and the `{facial,mouth,log,debug}` contract untouched. `schedule.js` stays pure (no rAF/DOM) for testability; `blendshapes.js` owns the rAF and the sink callbacks. `mouth.js` `delayMs=0` stays a pass-through.
**Alternative:** Fold into `mouth.delayMs` (desyncs face); standalone module between consumer and sinks (forces main.js wiring + constructor change for no benefit).

### DD-4: Clock across bursts / stream-splitting

**Decision:** One anchor per reply, re-anchored per burst via `now − t·1000`; new reply detected by `t` going backwards (`t < lastT − RESET_EPS`, `RESET_EPS = 500 ms`).
**Rationale:** `t` is reply-relative and monotone across sentences (`tts_plugin.py:410,433-442`), so a continuation burst keeps animating forward. Re-anchoring is self-correcting: if an inter-sentence gap tears the clock down (DD-5) and sentence 2 arrives with `t=2.4`, `anchorWall = now − 2400` makes the playhead ≈ 2.4 immediately — sentence-2 frames play forward from now, re-synced to the fresh audio, no multi-second freeze. A `t` reset to ~0 means a genuinely new reply (covers a dropped `{done}`): teardown the old, re-anchor.
**Alternative:** Single fixed anchor for the whole reply held across idle gaps — needs "keep clock alive but release face" bookkeeping; the re-anchor is simpler and more robust to A2F/audio latency drift between sentences.

### DD-5: Idle / end / flush semantics (REVISED during execution — see DD-8)

**Decision:** Teardown driven by bounded triggers, never on frames-pending:
- **Normal end:** `replyDone && buffer empty && playhead ≥ lastT` → `sink.onReplyEnd()` (face → idle). Exactly one reply-level `{done:true}` per reply arrives, on both normal completion and barge-in (`tts_plugin.py:28,477-483`). `markDone()` means only "all frames delivered; end when drained" — it does NOT arm any force-teardown (revision: the wire `done` fires at synthesis-complete, `tts_plugin.py:477-478` after `asyncio.gather`, which on long replies is seconds BEFORE playout ends; a grace cap keyed to it would truncate the tail of every long reply — the original DD-5 had this bug).
- **Audio-stopped cap (barge-in):** see DD-8 — `audioStopped()` (agent state leaves `speaking`) arms `deadline = now + AUDIO_STOP_GRACE_MS` (500). If `buffer non-empty && now ≥ deadline` → force teardown, discard the stale tail. Normal tails (≈ anchor latency + lag < 500 ms) drain before the deadline; a barge-in's multi-second stale tail is cut within 500 ms.
- **Dropped-`done` safety net:** `!replyDone && buffer empty && playhead ≥ lastT && (now − lastArrival) > IDLE_MS (250)` → teardown. Preserves the current idle net (`blendshapes.js:22,38-41`) but measured on drain-complete, not raw arrival — can never truncate queued animation.
- **`flush()` (disconnect):** synchronous teardown → `sink.onReplyEnd()` if a face was started; preserves the `room.js:88-93` invariant (`blendshapes.endStream()` → `mouth.endA2FStream()` before `lipsync.stop()`'s final `setMouthOpen(0)`), so the mouth closes instead of freezing open. `onReplyEnd` fires only if a frame was applied (`faceStarted`), keeping `mouth.beginA2FStream`/`endA2FStream` balanced.
**Alternative:** Keep arrival-gap idle as stream end (torn down while frames are queued → audio cuts face early); drain-everything-on-done (stale frames drain seconds after barge-in); grace cap armed by wire `done` (truncates long replies — rejected after Task 2 flagged it and `tts_plugin.py` confirmed done = synthesis-complete).

### DD-8: Barge-in signal = agent-state transition away from `speaking` (added during execution)

**Decision:** The frontend's authoritative audio-playout signal is the LiveKit agent state (`main.js:110` `onAgentState`, from `agent-state.js` watch): it stays `speaking` during actual playout and leaves `speaking` on completion AND on barge-in. On a `speaking → non-speaking` transition, `main.js` calls `blendshapes.audioStopped()` → `sched.audioStopped()` which arms the bounded drain grace above.
**Rationale:** The wire `{done}` cannot distinguish "synthesis finished, playout continues" from "barge-in, audio stopped" — the agent state can, with zero new plumbing (the hook already exists at `main.js:110` and `lastAgentState` is already tracked at `main.js:62`).
**Alternative:** Audio element `currentTime`/silence detection (new plumbing through room.js, rejected in DD-1); fixed grace on wire done (truncates long replies).

### DD-6: Debug hooks move to the apply path

**Decision:** Call `facedebug.js` `onStart/onFrame/onEnd` from the scheduler sink (apply-time), not on arrival. `facedebug.js` itself is not modified.
**Rationale:** Acceptance requires the `?facedebug=1` panel to show playback spread over the audio span, not `recv≈50 ms`. The panel measures the wall-time span of the calls it receives (`facedebug.js:76,84-88,96-100`); calling it at apply time makes `recv ≈ audio` span (ratio ≈1) and the schematic face moves continuously — exactly the fix verification.
**Alternative:** Keep arrival-time debug — the arrival measurement already did its job of finding the bug; post-fix we want to observe playback.

### DD-7: Testing approach

**Decision:** Pure `schedule.js` unit tests (fake clock, stub sink, manual `tick()`), plus a light `blendshapes.test.mjs` (injected `now` + `scheduleRaf` pump). `blendshapes.js` takes optional `{ now, scheduleRaf }` defaulting to `performance.now`/`requestAnimationFrame`.
**Rationale:** Matches the existing fake-clock style (`createMouth(avatar, { …, now })`, `mouth.test.mjs:37`); covers buffering/clock/late-frame/flush policies without DOM.
**Alternative:** Browser-only manual testing (leaves the untested-blendshapes gap open).

## Tasks

### Task 1: Create the pure playback scheduler `schedule.js`

- **Files:** `infra/pi/web/static/js/schedule.js` (create)
- **Depends on:** none
- **Scope:** M
- **What:** Pure buffer/clock/drain module: `createScheduler({ now, sink, lagMs=100, idleMs=250, doneGraceMs=250, resetEpsMs=500 })` with `push/markDone/tick/flush/active`, no DOM/rAF. `sink = { onReplyStart(), apply(frame), onReplyEnd() }`.
- **How:** Implement DD-1..DD-5. State: `anchorWall|null`, `buffer[]` (insertion-sorted by `t`), `lastTms`, `replyDone`, `faceStarted`, `lastArrivalWall`, `doneDeadline`. `push(evt)`: set `lastArrivalWall=now()`; if `anchorWall===null` or `evt.t*1000 < lastTms − resetEpsMs` → if a face was started, `sink.onReplyEnd()`; `anchorWall = now() − evt.t*1000`, reset state. Insertion-sort `evt` into `buffer` by `t`; `lastTms = max(lastTms, evt.t*1000)`. `markDone()`: if anchored, `replyDone=true; doneDeadline = now()+doneGraceMs`. `tick()`: if not anchored return false; drain `while(buffer[0] && anchorWall + buffer[0].t*1000 + lagMs <= now())` → `if(!faceStarted){faceStarted=true; sink.onReplyStart()} sink.apply(f)`; teardown when buffer empty & `now() >= anchorWall+lastTms+lagMs` under (`replyDone`) or (`!replyDone && now()−lastArrivalWall>idleMs`); barge-in cap: `replyDone && buffer.length && now()>=doneDeadline` → force teardown; teardown = `if(faceStarted) sink.onReplyEnd(); anchorWall=null; buffer=[]; faceStarted=false; replyDone=false`. `flush()` = teardown now. `tick()` returns false when idle so the caller can stop its rAF.
- **Context:** `infra/pi/web/static/js/mouth.js:15-26` (ring shape), `mouth.js:96-97` (injectable `now`), `docs/adr/0013-a2f-driven-lipsync-volume-fallback.md:36-40` (PTS buffering intent). Note `evt.t` is seconds, `now()` is ms.
- **Verify:** `node -e "import('./infra/pi/web/static/js/schedule.js').then(m=>console.log(typeof m.createScheduler))"` prints `function`.

### Task 2: Unit-test the scheduler `schedule.test.mjs`

- **Files:** `infra/pi/web/static/js/schedule.test.mjs` (create)
- **Depends on:** Task 1
- **Scope:** M
- **What:** Fake-clock tests for buffering, apply-spread, continuation, gap re-anchor, `t`-reset, barge-in cap, idle net, LAG, `t`-order, flush.
- **How:** Follow `mouth.test.mjs` style (`node:assert/strict`, eq/ok counters, `process.exit(0)`, print `schedule.js: all N assertions passed`). Stub `sink` records calls. Fake `clock` var, `now:()=>clock`. Cases: (a) one big burst t=0..~1s pushed within 50ms wall + `markDone` → step clock and `tick()`; assert applies spread ∝ `t`, never all in one tick, ≤2 per tick; (b) continuation gap<idle → single `onReplyStart`, continuous applies; (c) gap>idle → `onReplyEnd` then `onReplyStart` again, sentence-2 (`t=2.4`) applies within ~1 tick of its push, not 2.4s later; (d) `t`-reset (reply2 `t=0` while reply1 anchored) → teardown+new start; (e) barge-in: full sentence buffered, `markDone` early, advance `doneGraceMs` → teardown with buffer non-empty (remaining frames NOT all applied); (f) idle net: drain, no done, advance >idleMs → teardown; (g) LAG: frame `t=1` applied only when `clock≈anchor+1000+lagMs`; (h) out-of-order push applied in ascending `t`; (i) `flush()` → `onReplyEnd`, `active===false`.
- **Context:** `infra/pi/web/static/js/mouth.test.mjs:1-48` (harness + fake clock), `infra/pi/web/static/js/schedule.js` (Task 1 output).
- **Verify:** `node --test infra/pi/web/static/js/schedule.test.mjs` — green.

### Task 3: Wire the scheduler into `blendshapes.js`

- **Files:** `infra/pi/web/static/js/blendshapes.js` (change)
- **Depends on:** Task 1
- **Scope:** M
- **What:** Route DataChannel frames into the scheduler, drive it from an rAF, and move `facial.apply`/`mouth.ingestA2FFrame`/`debug.*` to the scheduler's apply/start/end sink. Keep the `wire/inject/endStream` API and `window.__a2fInject`.
- **How:** `createBlendshapes({ facial, mouth, log, debug, now, scheduleRaf })` (defaults `performance.now`, `requestAnimationFrame`/`cancelAnimationFrame` guarded by `typeof window`). Build `sink = { onReplyStart(){ mouth.beginA2FStream(); if(debug)debug.onStart(); if(log)log('a2f stream start'); }, apply(f){ if(debug)debug.onFrame(f); facial.apply(f.arkit); mouth.ingestA2FFrame(f); }, onReplyEnd(){ facial.release(); mouth.endA2FStream(); if(debug)debug.onEnd(); if(log)log('a2f stream end'); } }`. `const sched = createScheduler({ now, sink })`. `handle(evt)`: `blendshapes` type → `sched.push(evt); ensureLoop()`; `evt.done` → `sched.markDone(); ensureLoop()`. rAF loop: `tick(){ const active = sched.tick(); if(active) raf=scheduleRaf(tick); else raf=null; }`; `ensureLoop(){ if(!raf) raf=scheduleRaf(tick); }`. `endStream()` → `sched.flush()` + cancel raf. Remove old `active`/`idleTimer`/direct-apply logic (`:25-52`) and the `IDLE_MS` const (moved into scheduler defaults). Update the module header comment: buffering/timing live in `schedule.js`; debug hooks fire at apply time.
- **Context:** `infra/pi/web/static/js/blendshapes.js:24-77` (full current impl), `facial.js:12-18`, `mouth.js:121-146` (begin/ingest/end), `facedebug.js:74-107` (onStart/onFrame/onEnd), `room.js:88-93` (endStream-before-lipsync.stop invariant), `main.js:60` (constructor call — must stay compatible).
- **Verify:** `node -e "import('./infra/pi/web/static/js/blendshapes.js').then(m=>console.log(typeof m.createBlendshapes))"` prints `function`; no residual apply-on-arrival in `handleFrame`.

### Task 3b: Audio-stopped grace replaces done-grace (DD-5 revision + DD-8) — added during execution

- **Files:** `infra/pi/web/static/js/schedule.js` (change), `infra/pi/web/static/js/schedule.test.mjs` (change), `infra/pi/web/static/js/blendshapes.js` (change), `infra/pi/web/static/js/main.js` (change, ~2 lines)
- **Depends on:** Tasks 1–3
- **Scope:** M
- **What:** Remove the done-armed force-teardown from the scheduler (wire `done` = synthesis-complete, arrives mid-playout on long replies and would truncate the animation tail); add `audioStopped()` arming a 500 ms drain grace, wired from the agent-state `speaking → non-speaking` transition in `main.js`.
- **How:** schedule.js: `markDone()` only sets `replyDone`; new `audioStopped()` sets `stopDeadline = now()+audioStopGraceMs` (default 500, replaces doneGraceMs) when a reply is active; tick's force-teardown trigger becomes `buffer non-empty && stopDeadline armed && now ≥ stopDeadline`; teardown resets it. blendshapes.js: expose `audioStopped()` → `sched.audioStopped(); ensureLoop()`. main.js `onAgentState` (line ~110): before updating `lastAgentState`, `if (lastAgentState === 'speaking' && state !== 'speaking') blendshapes.audioStopped()`. schedule.test.mjs: replace the done-cap case with (e1) early done mid-playout → buffer drains fully, no truncation, teardown after last frame's playout; (e2) barge-in: `audioStopped()` with a multi-second buffered tail → force teardown within grace, not all frames applied; (e3) `audioStopped()` when idle → no-op.
- **Context:** current `schedule.js`, `schedule.test.mjs`, `blendshapes.js` (post Task 1–3), `main.js:60-62,108-112`, `tts_plugin.py:467-493` (done = synthesis-complete, read-only).
- **Verify:** `node --test infra/pi/web/static/js/schedule.test.mjs` green; smoke: early done does not truncate; `audioStopped` caps a stale tail.

### Task 4: Wiring test `blendshapes.test.mjs`

- **Files:** `infra/pi/web/static/js/blendshapes.test.mjs` (create)
- **Depends on:** Task 3
- **Scope:** S
- **What:** Verify `inject()` buffers and the injected rAF pump drives the sink (facial+mouth called together on apply; `endStream` releases).
- **How:** Stub `facial`/`mouth`/`debug` recording calls. Pass `now:()=>clock` and a `scheduleRaf` that stores callbacks for a manual pump. `inject({type:'blendshapes',t:0,arkit:{JawOpen:1}})`; assert nothing applied before a pump at the scheduled clock; advance clock, pump → assert `mouth.beginA2FStream` then `facial.apply`+`mouth.ingestA2FFrame` with the same frame, and `debug.onStart`/`onFrame` fired. `inject({done:true})`, advance past last `t`+lag, pump → `facial.release`+`mouth.endA2FStream`. Assert `endStream()` tears down immediately. Standalone script style; print `blendshapes.js: all N assertions passed`; `process.exit(0)`.
- **Context:** `infra/pi/web/static/js/mouth.test.mjs:33-46` (stub avatar + fake clock), `infra/pi/web/static/js/blendshapes.js` (new signature from Task 3).
- **Verify:** `node --test infra/pi/web/static/js/blendshapes.test.mjs` — green.

### Task 5: Validation

- **Files:** —
- **Depends on:** all
- **Scope:** S
- **What:** Run the full frontend test suite. No lint/type-check/build exists for the frontend (no package.json).
- **Context:** existing tests `arkit-map/lipsync/motion/mouth.test.mjs` must stay green; `main.js`/`facedebug.js`/`mouth.js` unchanged.
- **Verify:** `node --test infra/pi/web/static/js/*.test.mjs` — all pass (6 files: 4 existing + schedule + blendshapes).

## Execution

- **Mode:** sub-agents
- **Parallel:** true
- **Reasoning:** 4 implementation tasks; after Task 1, Task 2 and Task 3 touch disjoint files and both depend only on Task 1.
- **Order:**
  Group 1 (sequential): Task 1
  ─── barrier ───
  Group 2 (parallel): Task 2, Task 3
  ─── barrier ───
  Group 3 (sequential): Task 4
  ─── barrier ───
  Group 4 (sequential): Task 5

## Verification

- On `ai.priney.com`, the Live2D face (mouth open + eyes/brows) animates for the whole duration of each spoken reply, roughly in sync with the voice — not a sub-second flash. Verify with `?facedebug=1`: the debug face moves continuously during speech and the panel's per-stream line shows playback spread over the audio span (not `recv≈50 ms`).
- Mouth opening tracks speech; face releases to idle between turns (no frozen stale pose).
- Multi-sentence replies animate continuously across sentence boundaries.
- Volume-fallback (`?lipsync=volume`) still works; existing frontend tests stay green (`node --test infra/pi/web/static/js/*.test.mjs`).

## Materials

- Issue: https://github.com/prineycom/voice-agent/issues/38 (Epic #33; phases #34–#36)
- ADR-0012 (hybrid facial animation), ADR-0013 (A2F-driven lipsync + volume fallback + measure-first `delayMs`), ADR-0015 (A2F helper production)
- Server facts (read-only, confirmed): `tts_plugin.py:410-421` — `t` is reply-relative, monotone across sentences; `tts_plugin.py:28,477-483` — exactly one reply-level `{done:true}` per reply, sent on normal completion AND barge-in; `engine.py:85` — `t` in seconds
- Debug tooling: `?facedebug=1` (`facedebug.js` + `main.js` faceDebug branch), `window.__a2fStats`, `window.__a2fInject`
- Constraint: server side is production — frontend-only change; no data-channel contract changes
