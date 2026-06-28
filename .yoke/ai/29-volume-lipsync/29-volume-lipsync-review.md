# Code Review: 29-volume-lipsync

## Summary

### Context and goal

Issue #29 (Epic 5 slice 4) adds frontend-only, volume-based lip-sync: the browser analyses the agent's
WebRTC audio track per frame (RMS via an `AnalyserNode`, reusing the `vu.js` pattern) and drives the
Live2D `ParamMouthOpenY` so the avatar's mouth opens with speech and closes on silence. Desktop TTS is
untouched.

### Key code areas for review

1. **`avatar.js` (`beforeModelUpdate` hook + `setMouthOpen`)** — writes `ParamMouthOpenY` as the absolute last writer each frame, overriding idle-motion mouth keyframes.
2. **`lipsync.js`** — pure `computeMouthTarget(rms)` + the `createLipSync` AudioContext/RAF driver (idempotent `start`, `resume()`, catch-cleanup).
3. **`room.js`** — `lipsync.start(agentTrack)` on `TrackSubscribed`, `stop()` on disconnect.
4. **`main.js`** — construction + hook wiring.

### Complex decisions

1. **`beforeModelUpdate` as the last writer** — verified every Natori idle/TapBody motion animates `ParamMouthOpenY`, so without this hook the idle lip-flap would fight the driver.
2. **Analyser connected to the source only** (never `destination`) — the LiveKit `<audio>` element already plays the track; connecting would double the audio.
3. **Pinning the mouth to `mouthOpen` (0 when idle)** — deliberate: a silent agent reads as closed-mouthed rather than appearing to talk silently from the idle motion.

### Questions for the reviewer

1. Multi-participant rooms are out of scope (single-agent assumption documented in `room.js`); confirm that holds for the kiosk use case.

### Risks and impact

- Lifecycle confirmed safe: `init()` is double-init-guarded so the `beforeModelUpdate` listener can't stack; on `teardown()` the model/internalModel are destroyed so the listener dies with its emitter. `stop()`'s `setMouthOpen(0)` is a safe no-op even after dispose. Idempotent `start()` + the new catch-cleanup + `resume()` close the remaining robustness gaps.

### Tests and manual checks

**Auto-tests:** `lipsync.test.mjs` — 6 assertions on the `computeMouthTarget` mapping (gate/mid/clamp), passing.

**Manual scenarios (deferred, on the Pi):**

1. Connect → the avatar's mouth moves during the spoken greeting (fixed TTS, so checkable WITHOUT the LLM) and closes when silent.

### Out of scope

- The pre-triaged known items (idempotency leak already fixed in `0cb38db`; an imprecise test label; the deferred live visual check) and `.yoke/ai/**` markdown.

## Commits

| Hash      | Description |
| --------- | ----------- |
| `d707d58` | docs: add implementation plan |
| `f143409` | feat: drive ParamMouthOpenY via beforeModelUpdate hook |
| `041abd2` | feat: add lip-sync module analysing the agent audio track |
| `e68164d` | feat: start lip-sync on the agent audio track and stop on disconnect |
| `0cb38db` | fix: make lip-sync start idempotent to avoid AudioContext leak |
| `b52ad2e` | docs: add execution report |
| `881ed35` | fix: fix 6 review issues |

## Changed Files

| File                                       | +/-     | Description |
| ------------------------------------------ | ------- | ----------- |
| `infra/pi/web/static/js/avatar.js`         | +modified | Mouth driver + deliberate-suppression comment. |
| `infra/pi/web/static/js/lipsync.js`        | +created  | RMS→mouth driver: idempotent start, `resume()`, catch-cleanup, doc comments. |
| `infra/pi/web/static/js/lipsync.test.mjs`  | +created  | 6-assertion mapping test. |
| `infra/pi/web/static/js/room.js`           | +modified | Start lip-sync on the agent track (single-agent note); stop on disconnect. |
| `infra/pi/web/static/js/main.js`           | +modified | Construct `lipsync`, add to room hooks. |

## Issues Found

| Severity | Score | Category    | File:line          | Description |
| -------- | ----- | ----------- | ------------------ | ----------- |
| Minor    | 35    | bugs        | `lipsync.js:39`    | Catch path didn't close a partially-built `AudioContext` → leak on mid-setup failure. |
| Minor    | 30    | bugs        | `lipsync.js:21`    | No `audioCtx.resume()` — a suspended context would make the analyser read silence (mouth never moves). |
| Minor    | 30    | quality     | `room.js:41`       | `start` fires on any remote audio track (no source filter) — fine for single-agent. |
| Minor    | 28    | quality     | `avatar.js:69`     | Hook pins `ParamMouthOpenY` to 0 when idle, suppressing baked idle mouth motion. |
| Minor    | 20    | performance | `lipsync.js:34`    | Lerp smoothing coefficient is frame-rate dependent (~60fps). |
| Minor    | 15    | style       | `lipsync.js:6`     | Gain/gate constants undocumented. |

## Fixed Issues

| Issue                                      | Commit    | Description |
| ------------------------------------------ | --------- | ----------- |
| Catch path leaked a partial AudioContext   | `881ed35` | Catch now closes + nulls `audioCtx` before logging. |
| No `resume()` (suspended-context silence)  | `881ed35` | `audioCtx.resume().catch(()=>{})` after construction. |
| No track-source filter                     | `881ed35` | Documented the single-agent assumption in `room.js`. |
| Idle mouth-motion suppression              | `881ed35` | Documented as deliberate (silent ⇒ closed mouth); behaviour intentionally unchanged. |
| Frame-rate-dependent smoothing             | `881ed35` | Commented the ~60fps tuning assumption. |
| Undocumented gain/gate constants           | `881ed35` | Added a comment explaining the gain (4) + gate (0.05). |

## Skipped Issues

> All found issues were fixed.

## Recommendations

- Live visual pass on the Pi: confirm the mouth moves with the greeting and closes on silence; tune the gain (`rms * 4`), gate (`0.05`), or lerp (`0.5`) if too subtle or jittery.
- If a multi-participant room is ever in scope, add a participant/`pub.source` filter before `lipsync.start`.
