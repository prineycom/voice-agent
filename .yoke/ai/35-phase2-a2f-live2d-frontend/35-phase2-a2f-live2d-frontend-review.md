# Code Review: 35-phase2-a2f-live2d-frontend

## Summary

### Context and goal

Epic 8 Phase 2 consumes Audio2Face ARKit blendshape frames on the `voiceagent` DataChannel and drives the Live2D Hiyori rig: a pure `arkit-map.js` face mapper, a `facial.js` → `avatar.setFaceParams` pinning sink, a `mouth.js` controller that becomes the single caller of `avatar.setMouthOpen()` (A2F `JawOpen` primary + volume fallback + `?lipsync=volume` toggle), a `blendshapes.js` consumer with a dev injector, and removal of the frontend `.exp3.json` expression path (ADR-0012). The mouth-sync work ships apply-on-arrival + a `D=0` delay-line seam + a cross-correlator instrument (per ADR-0013 / grill).

### Key code areas for review

1. **`mouth.js:createMouth()`** — single mouth writer; provider gate A2F↔volume and the swallow-on-A2F behaviour of `volumeSink`.
2. **`mouth.js:crossCorrelateOffset()`** — offset instrument: sign convention, grid resample, overlap handling.
3. **`blendshapes.js:createBlendshapes()`** — stream boundary (first-frame / `{done}` / 250 ms idle), idle-timer lifecycle, topic guard.
4. **`room.js:onDisconnected()`** — teardown ordering of `blendshapes.endStream()` vs `lipsync.stop()`.
5. **`arkit-map.js:arkitToLive2D()`** — ARKit→Live2D formula signs/clamps; deliberate exclusion of `ParamMouthOpenY`.
6. **`avatar.js:setFaceParams()`** — per-frame absolute param pinning and release-to-null in the `beforeModelUpdate` hook.

### Complex decisions

1. **`D=0` delay-line seam** (`mouth.js:105`) — apply-on-arrival now, a genuine `applyA2F()` boundary left for Phase-3 pts-keyed buffering. Correct per ADR-0013 measure-first.
2. **Verbatim `lipsync.js` reuse via `mouth.volumeSink`** (`main.js:53`) — the analyser is untouched; `volumeSink` mirrors the `setMouthOpen` sink shape and taps the played-audio envelope for correlation. Deliberate (ADR-0013: never delete the volume path).
3. **Correlator overlap guard** (`mouth.js:53`) — after review, per-lag scoring now requires `minOverlap` samples so a large shift with few surviving samples can't win spuriously.

### Questions for the reviewer

1. The overlap-guard threshold `max(8, n/2)` is heuristic; is it strict enough once Phase 3 consumes the offset for delay tuning, or should the correlator return a confidence alongside the ms value?

### Risks and impact

- Behaviour change confined to the mouth path; when no A2F stream is active the volume analyser drives the mouth exactly as before (self-heals).
- No unbounded growth (envelope rings are time-bounded ~2 s), no listener leak (the Room is recreated per connect), idle timer cleared on every boundary.
- Browser-only edges (PIXI rig, LiveKit DataChannel) are unverifiable under Node — needs a manual browser pass before Phase 3 sign-off.

### Tests and manual checks

**Auto-tests:**
- `arkit-map.test.mjs` (20), `mouth.test.mjs` (7), `motion.test.mjs` (21), `lipsync.test.mjs` (9) — all pass.
- Node integration smoke (scratchpad): A2F-primary face+mouth, `forceVolume` gating, idle-timeout release, and the disconnect-order fix (old order froze the mouth open; new order closes it).

**Manual scenarios:**
1. Connect → `window.__a2fInject` a canned utterance → Hiyori blink/gaze/brows/mouth-form/mouth-open move.
2. `?lipsync=volume` → mouth driven by loudness, face neutral.
3. Disconnect mid-stream → mouth closes (not frozen open).

### Out of scope

- Phase 3: agent forwarding frames on `voiceagent`, adaptive delay-loop tuning + persisting `D`, barge-in decay, live latency measurement.
- `lipsync.js` unchanged (deliberate verbatim reuse).

## Commits

| Hash    | Description |
| ------- | ----------- |
| 63d827b | feat: pure arkitToLive2D face mapper + unit test |
| a566cfe | feat: absolute face-param sink to avatar rig |
| 7986f87 | feat: mouth controller (A2F/volume provider, D=0 seam, cross-correlator) + tests |
| 54fc3d5 | refactor: remove frontend .exp3.json expression path (ADR-0012), unstale JawOpen note |
| 92cb140 | feat: facial controller (arkit map → rig, pin/release) |
| 2eab739 | feat: voiceagent blendshape consumer + dev injector |
| 8223adb | feat: wire mouth/facial/blendshapes; route volume analyser through mouth.volumeSink |
| 14d4f5d | fix: fix 3 review issues |

## Changed Files

| File                                    | +/-      | Description |
| --------------------------------------- | -------- | ----------- |
| `infra/pi/web/static/js/arkit-map.js`   | +36      | Pure ARKit→Live2D face mapper |
| `infra/pi/web/static/js/arkit-map.test.mjs` | +51  | Unit test (20 assertions) |
| `infra/pi/web/static/js/avatar.js`      | +19      | `setFaceParams` sink + retained-`setExpression` note |
| `infra/pi/web/static/js/mouth.js`       | +147     | Mouth controller + correlator (overlap guard) |
| `infra/pi/web/static/js/mouth.test.mjs` | +48      | Unit test (7 assertions) |
| `infra/pi/web/static/js/facial.js`      | +20      | apply/release glue |
| `infra/pi/web/static/js/blendshapes.js` | +74      | `voiceagent` consumer + dev injector |
| `infra/pi/web/static/js/motion.js`      | +12/-16  | Removed `.exp3.json` expression path |
| `infra/pi/web/static/js/motion.test.mjs`| ~91      | Dropped expression assertions |
| `infra/pi/web/static/js/main.js`        | +14/-2   | Construct + wire mouth/facial/blendshapes; toggle |
| `infra/pi/web/static/js/room.js`        | +6       | Wire consumer; teardown order fix |
| `infra/desktop/a2f/arkit.py`            | +3/-2    | Unstale the JawOpen note (ADR-0013) |

## Issues Found

| Severity  | Score | Category | File:line | Description |
| --------- | ----- | -------- | --------- | ----------- |
| Important | 70    | bugs     | `infra/pi/web/static/js/room.js:88-89` | Disconnect during an active A2F stream: `lipsync.stop()`'s `setMouthOpen(0)` is swallowed by the A2F provider gate, then `endStream()` writes nothing → mouth freezes open (avatar ticker persists to `beforeunload`). |
| Minor     | 35    | quality  | `infra/pi/web/static/js/avatar.js:127` | `setExpression()` has no callers after the ADR-0012 expression removal; dead surface that could mislead. |
| Minor     | 25    | quality  | `infra/pi/web/static/js/mouth.js:29,53` | (a) `a2fMouthOpen` comment claimed `MouthFunnel` is "read but unused" — it is never read. (b) `crossCorrelateOffset` didn't penalise shrinking overlap at large lags, allowing spuriously high scores. |

## Fixed Issues

| Issue | Commit | Description |
| ----- | ------ | ----------- |
| Mouth frozen open on disconnect (`room.js`) | `14d4f5d` | Reordered teardown: `blendshapes.endStream()` before `lipsync.stop()`, so the final `0` routes through the volume provider and closes the mouth. Proven by a node repro (old order freezes, new order closes). |
| Dead `setExpression` surface (`avatar.js`) | `14d4f5d` | Added a comment: retained deliberately for the one-line `natori` revert, whose profile still drives `.exp3.json`. |
| Correlator comment + overlap bias (`mouth.js`) | `14d4f5d` | Corrected the `MouthFunnel` comment; added a `minOverlap = max(8, n/2)` guard so thin-overlap large lags are rejected. Tests still recover the known +40 ms offset. |

## Skipped Issues

**All found issues were fixed.**

## Recommendations

- Before Phase 3 sign-off, run the manual browser pass (PIXI/LiveKit can't be exercised under Node): confirm the rig moves under `window.__a2fInject`, the `?lipsync=volume` retreat, and the no-stream fallback.
- When Phase 3 starts consuming the correlator offset for delay tuning, consider returning a confidence/overlap-count alongside the ms value rather than relying on the heuristic overlap guard alone.
