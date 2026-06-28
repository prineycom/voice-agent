# Code Review: 28-agent-motion-emotion-tags

## Summary

### Context and goal

Epic 5 slice 3 makes the agent worker authoritative for the avatar's motion + emotion: it publishes
`{type:"motion",state,emotion}` on the `voiceagent` data topic (from `agent_state_changed` and from
inline `[emotion:xxx]` tags parsed out of the LLM stream in an `llm_node` override), strips the tags
before TTS and the transcript, and the frontend consumes the events with precedence over
`lk.agent.state`, mapping the emotion enum to a Natori expression.

### Key code areas for review

1. **`infra/pi/agent/agent.py`** — `publish_motion` (async scheduling), the `llm_node` override (strip + publish), and the `agent_state_changed` hook.
2. **`infra/pi/agent/motion_events.py`** — the streaming `EmotionTagStripper` invariants (well covered in `tests/test_motion_events.py`).
3. **`infra/pi/web/static/js/motion.js`** — `applyMotionEvent` / precedence / emotion→expression map; `ops.js` motion routing; `main.js` wiring.

### Complex decisions

1. **Stripping in `llm_node`** — both TTS and transcript consume its output, so one interception keeps tags out of audio AND chat; tool-call chunks pass through untouched.
2. **Async publish via `asyncio.create_task`** (`agent.py`) — the publish callbacks are sync (called from the generator / event hook), so the coroutine is scheduled, not awaited inline, and wrapped so a failed publish can't abort the turn.
3. **Motion debounce on the frontend** — `applyMotionEvent` only restarts the motion when the state changes, but always updates the expression, so per-tag emotion updates don't stutter the animation.

### Questions for the reviewer

1. End-to-end behaviour (tagged reply → matching expression, clean audio/transcript) is unverified while the LLM quota is exhausted — worth a live pass once it resets.

### Risks and impact

- The review caught a **Critical** runtime bug (motion events were never published — async coroutine discarded) before it shipped; now fixed. Remaining risk is purely the un-run live path.

### Tests and manual checks

**Auto-tests:** `tests/test_motion_events.py` (23 cases incl. the global no-leak invariant); `motion.test.mjs` (18 assertions: mapping, precedence, motion-debounce). Both pass.

**Manual scenarios (deferred to LLM availability):**

1. Speak to the agent → tagged reply produces the matching Natori expression + motion, with NO tag in audio or transcript.
2. State transitions (listening/thinking/speaking) drive the avatar via motion events, overriding `lk.agent.state`.

### Out of scope

- The pre-triaged known items (malformed-tag leak — already fixed in `05f2876`; permanent `lk.agent.state` override after disconnect; no agent-level integration test; deferred live test).

## Commits

| Hash      | Description |
| --------- | ----------- |
| `7f2647b` | docs: add implementation plan |
| `ce76265` | feat: add emotion-tag stripper and motion-event module |
| `7ef3710` | feat: instruct LLM to emit inline emotion tags in SOUL |
| `fd2b7aa` | feat: consume motion events on frontend with precedence |
| `05f2876` | fix: strip malformed `[emotion...]` tags so the marker never leaks |
| `b85bbd3` | feat: strip emotion tags in llm_node and publish motion events |
| `2ba7de7` | docs: add execution report |
| `1b39f46` | fix: fix 5 review issues |

## Changed Files

| File                                       | +/-      | Description |
| ------------------------------------------ | -------- | ----------- |
| `infra/pi/agent/agent.py`                  | +103/-9  | llm_node override, async motion publisher, state-change hook (+5-issue fixes). |
| `infra/pi/agent/motion_events.py`          | +157     | Emotion enum, streaming stripper, motion-event JSON. |
| `infra/pi/agent/tests/test_motion_events.py`| +257    | 23 stripper/JSON tests + no-leak invariant. |
| `infra/pi/agent/SOUL.md`                   | +6       | `## Эмоции` inline-tag instructions. |
| `infra/pi/web/static/js/motion.js`         | +22      | applyMotionEvent, precedence, emotion map, motion debounce. |
| `infra/pi/web/static/js/motion.test.mjs`   | +57      | applyMotionEvent + precedence + debounce assertions. |
| `infra/pi/web/static/js/ops.js`            | +3       | Route `type:"motion"` to `onMotion`. |
| `infra/pi/web/static/js/main.js`           | +2/-1    | Wire `onMotion → motion.applyMotionEvent`. |

## Issues Found

| Severity  | Score | Category    | File:line          | Description |
| --------- | ----- | ----------- | ------------------ | ----------- |
| Critical  | 90    | bugs        | `agent.py:229`     | `publish_motion` called async `publish_data` without await/create_task → no motion event ever sent. |
| Important | 55    | performance | `motion.js:39`     | `applyMotionEvent` restarted the motion on every event → animation stutter on each emotion tag. |
| Minor     | 40    | bugs        | `agent.py:120`     | Dropping a fully-consumed text chunk could drop a co-located `tool_calls` payload. |
| Minor     | 30    | quality     | `agent.py:114`     | Emotion event hardcoded `state="speaking"` → premature speaking pose. |
| Minor     | 25    | quality     | `agent.py:111`     | Publish in `_on_emotion` lacked exception containment. |

## Fixed Issues

| Issue                                  | Commit    | Description |
| -------------------------------------- | --------- | ----------- |
| Motion events never published (async)  | `1b39f46` | `publish_motion` now schedules `asyncio.create_task(_send())` with try/except logging (also fixes the #5 containment gap). |
| Motion-restart stutter                 | `1b39f46` | `applyMotionEvent` calls `playMotion` only when the state key changes; expression always updates. |
| Co-located tool_call dropped           | `1b39f46` | Chunk dropped only when `not cleaned and not delta.tool_calls`; otherwise cleaned + yielded. |
| Premature `speaking` pose              | `1b39f46` | Agent tracks `current_state` (set in the state hook); emotion events use it instead of literal `speaking`. |
| Unhandled publish exception            | `1b39f46` | `_send()` wraps the await in try/except so telemetry can't break the turn. |

## Skipped Issues

> All found issues were fixed.

## Recommendations

- Run the live end-to-end test once the LLM quota resets (see [[llm-litellm-routing]]): confirm a tagged reply yields the matching expression + motion and that NO `[emotion...]` tag appears in spoken audio or the transcript.
- The Critical async-publish bug is the kind only a live/integration test surfaces; consider a thin smoke test that asserts `publish_data` is actually scheduled if the agent harness ever gets test scaffolding.
