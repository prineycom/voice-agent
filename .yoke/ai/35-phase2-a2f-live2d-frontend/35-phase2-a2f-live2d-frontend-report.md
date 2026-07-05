# Report: 35-phase2-a2f-live2d-frontend

**Plan:** `.yoke/ai/35-phase2-a2f-live2d-frontend/35-phase2-a2f-live2d-frontend-plan.md`
**Mode:** sub-agents
**Status:** ✅ complete

Epic 8 Phase 2 — consume A2F ARKit blendshape frames on the `voiceagent` DataChannel and drive the Live2D Hiyori rig (eyes/gaze/brows/eye-squint/mouth-form + mouth-opening), with the pluggable mouth provider and the mouth-sync seam from the `/yoke:grill` outcome. Built against a dev injector; end-to-end verifiable without Phase 3.

## Tasks

| #   | Task                                            | Status  | Commit    | Concerns |
| --- | ----------------------------------------------- | ------- | --------- | -------- |
| 1   | `arkit-map.js` pure face mapper + test          | ✅ DONE | `63d827b` | —        |
| 2   | `avatar.js` absolute face-param sink            | ✅ DONE | `a566cfe` | —        |
| 3   | `mouth.js` controller (provider/delay/correlator) + test | ✅ DONE | `7986f87` | —        |
| 4   | Remove `.exp3.json` expression path + unstale note | ✅ DONE | `54fc3d5` | —        |
| 5   | `facial.js` controller                          | ✅ DONE | `92cb140` | —        |
| 6   | `blendshapes.js` consumer + dev injector        | ✅ DONE | `2eab739` | —        |
| 7   | Wiring: `main.js` + `room.js`                    | ✅ DONE | `8223adb` | —        |

## Post-implementation

| Step          | Status     | Commit |
| ------------- | ---------- | ------ |
| Validate      | ✅ pass    | —      |
| Documentation | ⏭️ skipped (update_docs: false) | — |
| Format        | ⏭️ N/A (no JS formatter configured) | — |

## Architecture delivered (per /yoke:grill DD-1…DD-6)

- **`arkit-map.js`** — pure `arkitToLive2D(arkit)`; loose-sync face only (eyes/gaze/brows/squint/mouth-form/cheek). Never emits `ParamMouthOpenY` (DD-2). PascalCase ARKit keys, missing → 0.
- **`avatar.js`** — rig sink gains `setFaceParams(map|null)`: pins the face map absolutely in the existing `beforeModelUpdate` hook, `null` releases to Live2D auto-blink/idle.
- **`mouth.js`** — the SINGLE caller of `avatar.setMouthOpen()`. Provider selector (A2F primary / volume fallback + `forceVolume`), `a2fMouthOpen(JawOpen·(1−MouthClose))` (DD-6), 30→60 fps lerp, empty `D=0` delay-line **seam** (`applyA2F`), and a pure `crossCorrelateOffset()` instrument (RMS-envelope ↔ JawOpen-envelope → signed ms). Reuses `lipsync.js` **verbatim** via `volumeSink`.
- **`facial.js`** — thin apply/release glue (`arkitToLive2D` → `avatar.setFaceParams`).
- **`blendshapes.js`** — `voiceagent` consumer mirroring `ops.js`; owns stream boundaries (first frame → `{done}` **or** ~250 ms idle timeout, DD-5); `window.__a2fInject` dev seam.
- **Wiring** — `?lipsync=volume` (DD-4) toggles `forceVolume`; `lipsync` now feeds `mouth.volumeSink` (lipsync.js untouched); consumer wired in `room.js` on connect, released on disconnect.
- **Cleanup** — frontend `.exp3.json` emotion→expression path removed from `motion.js` (ADR-0012); motion states unchanged; stale "JawOpen NOT used" note in `infra/desktop/a2f/arkit.py` corrected (ADR-0013).

## Validation

- `node infra/pi/web/static/js/*.test.mjs` ✅ — arkit-map (20), mouth (7), motion (21), lipsync (9) = 57 assertions, 0 failed.
  - `mouth.test.mjs` covers `a2fMouthOpen` shaping, `crossCorrelateOffset` recovering a known +40 ms offset, and `forceVolume` provider gating.
- `node --check` on all 8 modified/new `.js` ✅
- `python3 -c "ast.parse(arkit.py)"` ✅
- **End-to-end integration smoke** (scratchpad, not committed) — `blendshapes.inject()` → facial + mouth → stub avatar:
  - A2F-primary: face pinned per frame (smile mouth-form, frame-5 left blink), face map carries no `ParamMouthOpenY`, mouth peak > 0.5 from JawOpen, `{done}` releases the face, correlator logged `mouth sync offset: 30ms`.
  - `forceVolume`: A2F frames never move the mouth; volume analyser does; face still animates.
  - Idle-timeout: a dropped `{done}` releases the face after 250 ms.

## Changes summary

| File                                         | Action   | Description |
| -------------------------------------------- | -------- | ----------- |
| `infra/pi/web/static/js/arkit-map.js`        | created  | Pure ARKit→Live2D face mapper |
| `infra/pi/web/static/js/arkit-map.test.mjs`  | created  | Unit test (20 assertions) |
| `infra/pi/web/static/js/avatar.js`           | modified | `setFaceParams(map\|null)` absolute face sink |
| `infra/pi/web/static/js/mouth.js`            | created  | Mouth controller: provider/delay-seam/correlator |
| `infra/pi/web/static/js/mouth.test.mjs`      | created  | Unit test (7 assertions) |
| `infra/pi/web/static/js/facial.js`           | created  | apply/release glue |
| `infra/pi/web/static/js/blendshapes.js`      | created  | `voiceagent` consumer + `__a2fInject` |
| `infra/pi/web/static/js/motion.js`           | modified | Removed `.exp3.json` expression path |
| `infra/pi/web/static/js/motion.test.mjs`     | modified | Dropped expression assertions |
| `infra/pi/web/static/js/main.js`             | modified | Construct + wire mouth/facial/blendshapes; `?lipsync=volume` |
| `infra/pi/web/static/js/room.js`             | modified | Wire consumer on connect, release on disconnect |
| `infra/desktop/a2f/arkit.py`                 | modified | Unstale the JawOpen note (ADR-0013) |

## Commits

- `95295c4` docs: add implementation plan
- `63d827b` feat: pure arkitToLive2D face mapper + unit test
- `a566cfe` feat: absolute face-param sink to avatar rig
- `7986f87` feat: mouth controller (A2F/volume provider, D=0 delay seam, cross-correlator) + tests
- `54fc3d5` refactor: remove frontend .exp3.json expression path (ADR-0012), unstale JawOpen note
- `92cb140` feat: facial controller (arkit map → rig, pin/release)
- `2eab739` feat: voiceagent blendshape consumer + dev injector
- `8223adb` feat: wire mouth/facial/blendshapes; route volume analyser through mouth.volumeSink

## Deferred to Phase 3 (out of scope, per plan)

- Agent forwarding A2F frames onto `voiceagent` (preserving `t`/`arkit`) — the consumer contract is pinned; no live data until then.
- Enabling/tuning the **adaptive delay loop** on live audio (Phase 2 ships apply-on-arrival + the instrument + the `D=0` seam); persisting the converged `D` across streams.
- Barge-in decay driven by the real agent; live latency measurement.

## Manual sign-off still needed (browser-only)

The PIXI/LiveKit edges can't run under Node. Before Phase 3, eyeball in a browser: load the frontend, connect, run `window.__a2fInject(frame)` against a canned utterance and confirm Hiyori's blink/gaze/brows/mouth-form/mouth-open move; verify the `?lipsync=volume` retreat and the no-stream fallback.
