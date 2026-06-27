# Report: 28-agent-motion-emotion-tags

**Plan:** `.yoke/ai/28-agent-motion-emotion-tags/28-agent-motion-emotion-tags-plan.md`
**Mode:** sub-agents
**Status:** ✅ complete

## Tasks

| #   | Task                                              | Status  | Commit    | Concerns |
| --- | ------------------------------------------------- | ------- | --------- | -------- |
| 1   | Agent emotion module + tests                      | ✅ DONE | `ce76265` (+fix `05f2876`) | malformed-tag leak found in review, fixed |
| 2   | SOUL.md — emotion-tag instructions                | ✅ DONE | `7ef3710` | —        |
| 3   | Frontend — motion events with precedence          | ✅ DONE | `fd2b7aa` | —        |
| 4   | Wire stripping + motion publishing into agent.py  | ✅ DONE | `b85bbd3` | —        |
| 5   | Validation                                        | ✅ DONE | —         | —        |

## Post-implementation

| Step          | Status     | Commit |
| ------------- | ---------- | ------ |
| Validate      | ✅ pass    | —      |
| Documentation | ⏭️ skipped (no --update-docs) | — |
| Format        | ⏭️ N/A (no JS formatter; ruff/black not configured for this slice) | — |

## Reviews

- **Task 1** — review caught an **Important** correctness gap: malformed tags without a colon (`[emotion]`, `[emotion happy]`, `[emotionX z]`) leaked the `[emotion` marker to the output, defeating the module's purpose. Fixed in `05f2876` (broadened the complete-tag strip to any `[emotion…]`, kept the streaming/divergent-bracket losslessness, added 8 tests). Re-verified: 23 tests pass + adversarial leak check clean.
- **Task 3** — ✅ Approved. One Minor (after disconnect the avatar stays in its last motion-event pose since `lk.agent.state` is permanently overridden) — accepted per the precedence spec.
- **Task 4** — ✅ Approved. Tool-call chunk passthrough is airtight; `current_emotion` lifecycle + closure binding correct; greeting/health/VAD/tools unchanged; surgical diff. One Minor (no agent-level integration test) — consistent with the existing no-`test_agent.py` pattern.
- Task 2 (SOUL.md) verified by grep (all five enum words present).

## Validation

- `cd infra/pi/agent && .venv/bin/python -m pytest tests/ -q` → **93 passed**, 1 pre-existing silero DeprecationWarning.
- `python3 -m py_compile infra/pi/agent/agent.py infra/pi/agent/motion_events.py` ✅
- `node --check` on all 9 `static/js/*.js` ✅
- `node infra/pi/web/static/js/motion.test.mjs` → **16 assertions passed** ✅
- SOUL.md emotion enum: `happy neutral sad surprised thinking` all present ✅
- Adversarial stripper leak check (`[emotion]`, `[emotion happy]`, split tags, legit `array[0]`/`[stuff]`): no `[emotion` ever leaks, legitimate brackets untouched ✅

> **End-to-end NOT verified** — the LLM quota (ollama.com weekly limit, see [[llm-litellm-routing]]) is exhausted, so a live tagged reply → clean audio/transcript + matching expression could not be exercised. The high-risk logic (streaming tag parse/strip, frontend emotion map + precedence) is covered by unit tests; the agent-side `llm_node`/`agent_state_changed` wiring is reasoned-through + py_compile/pytest-clean. Re-run a live test once the LLM is back.

## Design notes

- **Motion event:** `{"type":"motion","state":"<idle|listening|thinking|speaking>","emotion":"<enum>"}` on the existing `voiceagent` topic.
- **Emotion enum** `neutral|happy|sad|surprised|thinking` is the shared contract across `motion_events.py`, SOUL.md, and the frontend `EMOTION_EXPR` map; unknown/malformed → `neutral`. Agent sends the enum; the frontend maps to Natori expressions (`neutral→Normal, happy→Smile, sad→Sad, surprised→Surprised, thinking→Blushing`).
- **Stripping point:** `Agent.llm_node` override — both TTS and transcript consume its output, so one interception keeps tags out of audio AND chat. Tool-call chunks pass through untouched; the stripper is streaming (handles tags split across chunks) and now also strips malformed complete `[emotion…]` tags.
- **Authoritative motion:** published on `agent_state_changed` (state) + on each parsed emotion (speaking + emotion). Frontend motion events take permanent precedence over `lk.agent.state`.

## Changes summary

| File                                            | Action   | Description |
| ----------------------------------------------- | -------- | ----------- |
| `infra/pi/agent/motion_events.py`               | created  | Emotion enum, streaming `EmotionTagStripper`, `motion_event_json`. |
| `infra/pi/agent/tests/test_motion_events.py`    | created  | 23 tests: streaming/split/malformed tags, never-leak invariant, JSON. |
| `infra/pi/agent/SOUL.md`                         | modified | `## Эмоции` section instructing inline `[emotion:X]` tags. |
| `infra/pi/agent/agent.py`                        | modified | `llm_node` override (strip + publish), `agent_state_changed` publisher. |
| `infra/pi/web/static/js/motion.js`              | modified | `applyMotionEvent`, precedence flag, emotion→expression map. |
| `infra/pi/web/static/js/ops.js`                 | modified | Route `type:"motion"` to an `onMotion` callback. |
| `infra/pi/web/static/js/main.js`                | modified | Wire `onMotion → motion.applyMotionEvent`. |
| `infra/pi/web/static/js/motion.test.mjs`        | modified | +applyMotionEvent + precedence assertions (16 total). |

## Commits

- `7f2647b` #28 docs: add implementation plan
- `ce76265` #28 feat: add emotion-tag stripper and motion-event module
- `7ef3710` #28 feat: instruct LLM to emit inline emotion tags in SOUL
- `fd2b7aa` #28 feat: consume motion events on frontend with precedence over agent state
- `05f2876` #28 fix: strip malformed [emotion...] tags so the marker never leaks
- `b85bbd3` #28 feat: strip emotion tags in llm_node and publish authoritative motion events

## Follow-ups

- **Live end-to-end test** once the LLM quota resets: speak to the agent, confirm a tagged reply produces the matching Natori expression + motion, with NO tag in spoken audio or the transcript.
- Slice 4 (#29) adds volume-based lip-sync; slice 5 (#30) responsive polish.
