# Report: 29-volume-lipsync

**Plan:** `.yoke/ai/29-volume-lipsync/29-volume-lipsync-plan.md`
**Mode:** sub-agents
**Status:** ✅ complete

## Tasks

| #   | Task                                              | Status  | Commit    | Concerns |
| --- | ------------------------------------------------- | ------- | --------- | -------- |
| 1   | avatar.js — mouth-open driver (`beforeModelUpdate`)| ✅ DONE | `f143409` | —        |
| 2   | lipsync.js — agent-track RMS → mouth + test        | ✅ DONE | `041abd2` | —        |
| 3   | Wire lip-sync into room.js + main.js               | ✅ DONE | `e68164d` | —        |
| 4   | Validation                                         | ✅ DONE | —         | —        |

## Post-implementation

| Step          | Status     | Commit |
| ------------- | ---------- | ------ |
| Validate      | ✅ pass    | —      |
| Documentation | ⏭️ skipped (no --update-docs) | — |
| Format        | ⏭️ N/A (no JS formatter configured) | — |
| Polish        | ✅ idempotent `start()` (review Minor) | `0cb38db` |

## Reviews

- **Tasks 1+2** — ✅ Approved. `beforeModelUpdate` hook correctly used (not a bare RAF), absolute `setParameterValueById`, analyser connected source→analyser only (NOT destination — no double audio), `stop()` fully tears down + closes the mouth. One trivial Minor (a test-message label) — no action.
- **Task 3** — ✅ Approved. Lip-sync wired to the agent's audio track (not the mic), optional-guarded, room.js stays DOM-free, only 2 files changed. One Minor (non-idempotent `start` could leak an AudioContext on a second audio track) — **fixed** in `0cb38db` (`start()` now tears down any prior stream first).

## Validation

- `node --check` on all `static/js/*.js` (incl. new lipsync.js) ✅
- `node infra/pi/web/static/js/lipsync.test.mjs` → **6 assertions passed** (computeMouthTarget: 0→0, sub-gate→0, mid 0.1→0.4, clamp 1→1, just-over-gate non-zero) ✅
- `node infra/pi/web/static/js/motion.test.mjs` → 18 assertions passed (no regression) ✅
- Live serve: `GET /static/js/lipsync.js` → 200 `text/javascript` ✅

> **Live visual check pending** (manual, on the Pi): connect and confirm the avatar's mouth moves with
> the spoken audio and closes on silence. NOTE: the agent's greeting is fixed TTS (no LLM), so lip-sync
> is verifiable on the Pi **even while the LLM quota is exhausted** — the mouth should move during the
> greeting on connect.

## Design notes

- **`ParamMouthOpenY` written inside `model.internalModel.on('beforeModelUpdate', …)`** — fires after the motion/physics pass, last writer before the frame commits, so the absolute set overrides the idle motion's mouth keyframes (a bare RAF setter would race the library ticker and flicker). Verified against the `pixi-live2d-display@0.4.0` cubism4 bundle.
- **`lipsync.js`** mirrors `vu.js`: own AudioContext/AnalyserNode/RAF; RMS×4 gain, noise gate `<0.05→0` (full close on silence), lerp 0.5 smoothing; analyser connected to the source only (the track already plays via the LiveKit `<audio>` element). `avatar.setMouthOpen(0..1)` keeps `lipsync.js` free of Live2D internals.
- **Lifecycle** mirrors vu/ops: `lipsync.start(agentTrack)` in `room.js` `TrackSubscribed`, `lipsync.stop()` in `onDisconnected`. Independent of the `speaking` motion + expressions; Desktop TTS untouched.

## Changes summary

| File                                       | Action   | Description |
| ------------------------------------------ | -------- | ----------- |
| `infra/pi/web/static/js/avatar.js`         | modified | `mouthOpen` state, `beforeModelUpdate` → `ParamMouthOpenY`, `setMouthOpen(0..1)`. |
| `infra/pi/web/static/js/lipsync.js`        | created  | `computeMouthTarget`, `createLipSync` (RMS analyser → `avatar.setMouthOpen`), idempotent start. |
| `infra/pi/web/static/js/lipsync.test.mjs`  | created  | 6-assertion unit test for the RMS→mouth mapping. |
| `infra/pi/web/static/js/room.js`           | modified | Start lip-sync on the agent track; stop on disconnect. |
| `infra/pi/web/static/js/main.js`           | modified | Construct `lipsync`, pass it in the room hooks. |

## Commits

- `d707d58` #29 docs: add implementation plan
- `f143409` #29 feat: drive ParamMouthOpenY via beforeModelUpdate hook
- `041abd2` #29 feat: add lip-sync module analysing the agent audio track
- `e68164d` #29 feat: start lip-sync on the agent audio track and stop on disconnect
- `0cb38db` #29 fix: make lip-sync start idempotent to avoid AudioContext leak

## Follow-ups

- Live visual pass on the Pi (mouth moves with the greeting). Tune `GAIN`/gate/smoothing if the mouth is too subtle or too jittery.
- Slice 5 (#30) — responsive desktop+mobile + polish — is the last Epic 5 slice.
