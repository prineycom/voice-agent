# Code Review: 38-a2f-playback-scheduler

## Summary

### Context and goal

A2F blendshape frames arrive on the LiveKit `voiceagent` DataChannel in bursts ~200x faster than real time with correct reply-relative `t`; the old frontend applied them on arrival, so the Live2D face flashed through a reply and froze (issue #38). This change inserts a playback scheduler (`schedule.js`) that buffers frames and drains each at `anchor + t·1000 + lagMs` on an rAF loop, wired through `blendshapes.js` with an agent-state-driven audio-stop grace (`main.js`). Frontend-only; server untouched.

### Key code areas for review

1. **`schedule.js:tick()`** — the drain/teardown state machine; all four teardown triggers (normal end, audio-stop grace, dropped-done idle net, flush) are arbitrated here.
2. **`schedule.js:push()`** — anchor/re-anchor policy (`now − t·1000`, backward-`t` reset), frame validation, and grace disarm on live frames.
3. **`schedule.js:audioStopped()`** — the barge-in/playout-end signal arming the 500 ms drain grace.
4. **`blendshapes.js` sink + rAF loop** — atomic face+mouth+debug apply at scheduled time; self-stopping loop; `endStream()` synchronous flush.
5. **`main.js:onAgentState`** — `speaking → non-speaking` transition → `blendshapes.audioStopped()`.
6. **`mouth.js:crossCorrelateOffset` semantics** — its measured offset now includes the scheduler's constant lag (comments updated).

### Complex decisions

1. **Anchor = first-frame arrival, not audio position** (`schedule.js`) — the face trails the voice by A2F latency + 100 ms as a bounded constant lag (ADR-0013-sanctioned); avoids new plumbing for the LiveKit audio element's `currentTime`.
2. **Wire `{done}` ≠ audio stopped** (`schedule.js`, `tts_plugin.py:477`) — `done` fires at synthesis-complete (mid-playout on long replies), so it only means "end when drained"; the bounded teardown comes from the agent-state transition, which tracks actual playout and barge-in.
3. **Grace disarm on push** (review fix, `schedule.js`) — any newly arriving valid frame disarms the audio-stop grace; a stale stop landing between sentence bursts can still truncate, but the next burst re-anchors and recovers (bounded, self-healing) — chosen over a generation-counter guard for simplicity.

### Questions for the reviewer

1. Can the agent state flap `speaking → x → speaking` mid-reply with NO frames arriving in that window (e.g. long tool-call pause mid-turn with TTS already fully forwarded)? The grace would truncate the tail; today's flows always have `done` + drained buffer or arriving frames there, but a future mid-turn pause pattern would need the generation guard.
2. Is a 100 ms scheduler lag + A2F transport latency an acceptable constant face-behind-voice offset, or should `lagMs` be tuned after measuring with `crossCorrelateOffset()` on production?

### Risks and impact

- Behavior change for `?facedebug=1`: the panel now measures playback pacing (ratio ≈ 1x is healthy); it no longer shows wire-arrival bursts.
- `mouth.js` cross-correlator readings now include the constant scheduler lag — historical comparisons with pre-fix measurements are apples-to-oranges.
- No performance risk: buffer is bounded per reply (~300 frames), drain is O(applied) per tick, loop self-stops when idle.

### Tests and manual checks

**Auto-tests:**

- `node --test infra/pi/web/static/js/*.test.mjs` — 6 files, 0 failures (schedule 75 assertions incl. state-flap and malformed-`t` cases; blendshapes 29 assertions).

**Manual scenarios (post-deploy, ai.priney.com):**

1. Ask a long multi-sentence question with `?facedebug=1` → debug face moves continuously through the whole reply; panel per-stream line shows playback spread ≈ audio span (ratio ≈ 1x).
2. Interrupt the agent mid-reply → face releases to idle within ~500 ms of the audio stopping.
3. `?lipsync=volume` → volume fallback mouth still works.

### Out of scope

- Server side (A2F helper, TTS→A2F fork, agent forward) — production, untouched.
- Tuning `lagMs` from measured cross-correlator offsets (follow-up once production data exists).
- avatar.js / room.js / facial.js / lipsync.js — read for contracts, unchanged.

## Commits

| Hash      | Description                                                                          |
| --------- | ------------------------------------------------------------------------------------ |
| `d25479b` | docs(38-a2f-playback-scheduler): add implementation plan                              |
| `e10e230` | feat(38-a2f-playback-scheduler): add pure blendshape playback scheduler               |
| `d1c73c3` | feat(38-a2f-playback-scheduler): route blendshape frames through playback scheduler   |
| `67ae427` | test(38-a2f-playback-scheduler): unit tests for playback scheduler                    |
| `e892073` | docs(38-a2f-playback-scheduler): revise DD-5, add DD-8 + Task 3b (audio-stopped grace)|
| `ddacae6` | fix(38-a2f-playback-scheduler): bound reply teardown by agent audio-stop, not wire done|
| `7e7300b` | test(38-a2f-playback-scheduler): wiring test for scheduled blendshape playback        |
| `1ba9277` | docs(38-a2f-playback-scheduler): add execution report                                 |
| `b324eb6` | fix(38-a2f-playback-scheduler): fix 4 review issues                                   |

## Changed Files

| File                                        | +/-      | Description                                                        |
| ------------------------------------------- | -------- | ------------------------------------------------------------------ |
| infra/pi/web/static/js/schedule.js          | +122     | New pure playback scheduler (buffer/anchor/drain/teardown)          |
| infra/pi/web/static/js/schedule.test.mjs    | +316     | 75 fake-clock assertions incl. flap + malformed-`t` regression cases |
| infra/pi/web/static/js/blendshapes.js       | +96/-~25 | Frames routed through scheduler; rAF drain loop; apply-time sink    |
| infra/pi/web/static/js/blendshapes.test.mjs | +161     | 29 wiring assertions                                                |
| infra/pi/web/static/js/main.js              | +9/-2    | speaking→non-speaking transition → audioStopped()                   |
| infra/pi/web/static/js/facedebug.js         | +17/-    | Comment-only: panel measures playback pacing now                    |
| infra/pi/web/static/js/mouth.js             | +22/-    | Comment-only: Phase-3 buffering lives in schedule.js; offset semantics |

## Issues Found

| Severity  | Score | Category      | File:line                | Description                                                                 |
| --------- | ----- | ------------- | ------------------------ | --------------------------------------------------------------------------- |
| Important | 75    | bugs          | schedule.js:75-98        | stopDeadline not tied to its reply; state flap / stale stop truncates a live reply |
| Important | 65    | bugs          | schedule.js:62           | Malformed frame `t` (NaN/undefined) poisons lastTms; normal-end/idle teardowns never fire |
| Minor     | 40    | documentation | facedebug.js:2-4         | Header still claimed wire-arrival semantics after debug hooks moved to apply time |
| Minor     | 30    | documentation | mouth.js:9-11,105-111    | Stale Phase-3 seam comment; cross-correlator offset now includes scheduler lag |

## Fixed Issues

| Issue                                        | Commit    | Description                                                                    |
| -------------------------------------------- | --------- | ------------------------------------------------------------------------------ |
| stopDeadline truncates live reply (75)       | `b324eb6` | `push()` disarms the grace on every valid frame; +state-flap regression test    |
| NaN `t` poisons lastTms (65)                 | `b324eb6` | `push()` drops frames with missing/non-finite/negative `t` before touching state; +3 regression tests |
| facedebug.js stale arrival-semantics comment | `b324eb6` | Comment-only rewrite: panel measures playback pacing; ratio ≈ 1x is healthy     |
| mouth.js stale Phase-3/offset comments       | `b324eb6` | Comment-only rewrite: buffering lives in schedule.js; offset includes lagMs     |

## Skipped Issues

**All found issues were fixed.**

## Recommendations

- After deploying to the Pi, run the three manual scenarios above (facedebug continuity, barge-in release, volume fallback) — unit tests cover the timing logic but not the live LiveKit/agent-state event stream.
- Consider measuring `crossCorrelateOffset()` on production replies and tuning `schedule.js`'s `lagMs` if the constant face-behind-voice offset is noticeable.
- If a future agent flow introduces mid-turn `speaking` pauses with fully-forwarded frames, add a generation guard to `audioStopped()` (see Questions #1).
