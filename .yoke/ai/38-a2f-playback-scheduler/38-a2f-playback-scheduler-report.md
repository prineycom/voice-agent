# Report: 38-a2f-playback-scheduler

**Plan:** .yoke/ai/38-a2f-playback-scheduler/38-a2f-playback-scheduler-plan.md
**Mode:** sub-agents
**Status:** ✅ complete

## Tasks

| #   | Task                                                | Status                | Commit    | Concerns  |
| --- | --------------------------------------------------- | --------------------- | --------- | --------- |
| 1   | Pure playback scheduler `schedule.js`               | ✅ DONE               | `e10e230` | —         |
| 2   | Scheduler unit tests `schedule.test.mjs`            | ⚠️ DONE_WITH_CONCERNS | `67ae427` | see below |
| 3   | Wire scheduler into `blendshapes.js`                | ✅ DONE               | `d1c73c3` | —         |
| 3b  | Audio-stopped grace replaces done-grace (DD-5/DD-8) | ✅ DONE               | `ddacae6` | —         |
| 4   | Wiring test `blendshapes.test.mjs`                  | ✅ DONE               | `7e7300b` | —         |
| 5   | Validation (full suite)                             | ✅ DONE               | —         | —         |

## Post-implementation

| Step          | Status                                                              | Commit |
| ------------- | ------------------------------------------------------------------- | ------ |
| Validate      | ✅ pass (6 test files, 0 failures)                                   | —      |
| Documentation | ⏭️ skipped (no `--update-docs`)                                      | —      |
| Format        | ✅ N/A — no formatter configured (vanilla ES modules, no build step) | —      |

## Concerns

### Task 2: Scheduler unit tests

The test executor flagged that the original DD-5 design armed a 250 ms force-teardown on the wire `{done:true}` event. Verified against `infra/pi/agent/tts_plugin.py:477-478`: the reply-level `done` is sent at **synthesis-complete** (right after `asyncio.gather`), which on long replies is seconds before audio playout ends — the cap would have frozen the face for the back half of every long reply, reproducing the original bug. **Resolved by Task 3b** (added mid-run, plan amended in commit `e892073`): `markDone()` now only means "all frames delivered; end when drained", and a new `audioStopped()` signal — wired from the LiveKit agent-state `speaking → non-speaking` transition in `main.js` (which tracks actual playout and fires on barge-in) — arms a bounded 500 ms drain grace that discards a stale tail.

## Validation

`node --test infra/pi/web/static/js/*.test.mjs` ✅ (6 pass, 0 fail: arkit-map, blendshapes 29 asserts, lipsync, motion, mouth, schedule 62 asserts)
Lint / type-check / build — N/A (no tooling configured for the frontend)

## Changes summary

| File                                          | Action   | Description                                                                                                                                                             |
| --------------------------------------------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| infra/pi/web/static/js/schedule.js            | created  | Pure buffer/clock/drain scheduler: anchors each burst at `now − t·1000`, drains frames at `anchor + t + lag(100ms)`, teardown via normal-end / audio-stop grace (500ms) / idle net (250ms) / flush |
| infra/pi/web/static/js/blendshapes.js         | modified | Frames now routed into the scheduler; self-stopping rAF drain loop; facial+mouth+debug fire atomically at apply time (debug panel now measures playback, not arrival); new `audioStopped()` API |
| infra/pi/web/static/js/main.js                | modified | `onAgentState` hook: `speaking → non-speaking` transition calls `blendshapes.audioStopped()`                                                                            |
| infra/pi/web/static/js/schedule.test.mjs      | created  | 62 fake-clock assertions: apply-spread, continuation, re-anchor, t-reset, early-done no-truncate, audio-stop cap, idle net, lag, ordering, flush                        |
| infra/pi/web/static/js/blendshapes.test.mjs   | created  | 29 wiring assertions: buffer-then-apply order, same-object identity, done→drain→release, grace cap, endStream teardown + rAF cancel, `__a2fInject`                      |

Untouched (per plan): `mouth.js`, `facial.js`, `facedebug.js`, `avatar.js`, `room.js`, all server-side code.

## Commits

- `d25479b` #38 docs(38-a2f-playback-scheduler): add implementation plan
- `e10e230` #38 feat(38-a2f-playback-scheduler): add pure blendshape playback scheduler
- `d1c73c3` #38 feat(38-a2f-playback-scheduler): route blendshape frames through playback scheduler
- `67ae427` #38 test(38-a2f-playback-scheduler): unit tests for playback scheduler
- `e892073` #38 docs(38-a2f-playback-scheduler): revise DD-5, add DD-8 + Task 3b (audio-stopped grace)
- `ddacae6` #38 fix(38-a2f-playback-scheduler): bound reply teardown by agent audio-stop, not wire done
- `7e7300b` #38 test(38-a2f-playback-scheduler): wiring test for scheduled blendshape playback

## Browser verification still pending

Unit tests cover the timing logic; the acceptance criteria on `ai.priney.com` (face animates for the whole spoken reply with `?facedebug=1`, multi-sentence continuity, `?lipsync=volume` fallback) need a live check after deploy. The facedebug panel's per-stream line should now show `recv` ≈ `audio` span (ratio ≈ 1x) instead of `recv≈50 ms`.
