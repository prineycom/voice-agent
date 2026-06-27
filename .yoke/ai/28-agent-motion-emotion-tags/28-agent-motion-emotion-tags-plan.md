# Agent-authoritative motion events + LLM emotion tags — implementation plan

**Task:** GitHub issue #28 (Epic 5 slice 3)
**Complexity:** complex
**Mode:** sub-agents
**Parallel:** true

## Design decisions

### DD-1: Motion event schema on the existing `voiceagent` topic

**Decision:** The Agent Worker publishes `{"type":"motion","state":"<idle|listening|thinking|speaking>","emotion":"<neutral|happy|sad|surprised|thinking>"}` JSON on the existing LiveKit data topic `voiceagent` (`UI_TOPIC` in `hermes_tasks.py:32`), alongside `tasks`/`event`, via `ctx.room.local_participant.publish_data(payload, reliable=True, topic=UI_TOPIC)`.
**Rationale:** ADR-0009 says the topic "gains a `motion` message type alongside `tasks`/`event`"; reuses the exact publish path already used for Hermes ops (`agent.py:169-173`) and the frontend's existing `voiceagent` subscription (`ops.js` `DataReceived`).
**Alternative:** A separate topic/data channel — rejected; ADR-0009 mandates the same topic.

### DD-2: Emotion enum is the shared contract; agent sends the enum, frontend maps to `.exp3.json`

**Decision:** Fixed enum `neutral | happy | sad | surprised | thinking`. The agent emits the **enum value** in the motion event (model-agnostic); the **frontend** maps enum → Natori expression name. Unknown/missing → `neutral`. The enum is defined in the agent's `motion_events.py`, mirrored in SOUL.md, and in the frontend emotion map — three in-sync copies, called out here as the single source of truth.
**Rationale:** ADR-0009: "the frontend maps enum → `.exp3.json`, unknown → `neutral`." Keeping the agent model-agnostic means swapping the Live2D model only touches the frontend map.
**Alternative:** Agent sends the raw Natori expression name — rejected; couples the worker to a specific model.

### DD-3: Strip emotion tags in an `Agent.llm_node` override (before TTS AND transcript)

**Decision:** Override `llm_node` on the agent. Wrap the default LLM stream; for each `ChatChunk` whose `delta.content` holds text, run the text through a stateful `EmotionTagStripper` and **mutate `chunk.delta.content`** to the cleaned text before yielding; pass tool-call/contentless chunks through untouched. Tags are `[emotion:<word>]` (case-insensitive, optional inner spaces).
**Rationale:** Both TTS (`tts_node`) and the transcript consume the output of `llm_node`, so stripping there guarantees tags reach neither the TTS engine nor the chat (AC). `ChatChunk.delta` is a mutable pydantic `ChoiceDelta` with `content: str | None` (verified in `llm/llm.py:71-81`), so in-place mutation is safe and preserves tool-call chunks. The stripper is **stateful/streaming** because a tag can split across chunks (`[emo` + `tion:happy]`): it buffers a tail that could be a partial tag and only emits text it is sure is outside a tag.
**Alternative:** Strip in `tts_node` only — rejected; the transcript would still show the tag. Post-process the final transcript — rejected; TTS already spoke the tag.

### DD-4: Authoritative motion = state changes + parsed emotion

**Decision:** Publish a motion event on every `session.on("agent_state_changed")` (carrying `new_state` + the current emotion) AND whenever the stripper parses an emotion mid-reply (carrying `state="speaking"` + the new emotion). The agent holds `current_emotion` (default `neutral`), reset to `neutral` at the start of each `llm_node` turn; the parsed tag updates it. `AgentState` values (`initializing|listening|thinking|speaking`) pass through; the frontend's `resolve()` maps `initializing`→`idle`.
**Rationale:** The frontend gives motion events precedence over `lk.agent.state` permanently (DD-5), so the agent must drive ALL state transitions or the avatar would freeze on the last event. `agent_state_changed` (verified in `voice/events.py:307`, `new_state: AgentState`) is the authoritative source.
**Alternative:** Publish only on emotion tags — rejected; avatar would stick in `speaking` and never show `listening`/`thinking` again under the precedence model.

### DD-5: Frontend — motion events take precedence over `lk.agent.state`

**Decision:** `motion.js` gains `applyMotionEvent({state, emotion})` and an internal `motionEventActive` flag. The first motion event sets the flag; thereafter `setState(state)` (driven by `lk.agent.state`) is ignored. `applyMotionEvent` sets the motion group from `state` (via the existing `resolve()`+table) and the expression from an emotion→Natori-name map: `neutral→Normal, happy→Smile, sad→Sad, surprised→Surprised, thinking→Blushing`; unknown → `Normal` (neutral). `ops.js` routes `evt.type === 'motion'` to an injected `onMotion` callback; `main.js` wires `onMotion: (evt) => motion.applyMotionEvent(evt)`.
**Rationale:** ADR-0009/AC: motion events "taking precedence over `lk.agent.state`". Reuses the slice-2 controller (`resolve()`, the group/index table) and avatar `setExpression` (`avatar.js`), and the existing single `voiceagent` subscription in `ops.js`.
**Alternative:** Precedence per-message (fall back to `lk.agent.state` between events) — rejected; flapping between two sources looks jittery and contradicts "authoritative".

### DD-6: Emotion-tag parsing/stripping is unit-tested (agent) + frontend map tested

**Decision:** `tests/test_motion_events.py` covers the `EmotionTagStripper`: single chunk, tag split across chunks, multiple tags, tag at start/end, no tag, partial-tag-lookalike text that is NOT a tag, unknown emotion → `neutral`, and that the cleaned text never contains `[emotion`. Extend `motion.test.mjs` for `applyMotionEvent` (emotion→expression) + precedence (lk.agent.state ignored after a motion event).
**Rationale:** AC explicitly requires "Tests cover tag parsing/stripping in the Agent Worker"; the streaming split-tag case is the highest-risk logic and the only piece fully testable while the LLM is down.

## Tasks

### Task 1: Agent emotion module + tests

- **Files:** `infra/pi/agent/motion_events.py` (create), `infra/pi/agent/tests/test_motion_events.py` (create)
- **Depends on:** none
- **Scope:** M
- **What:** Pure-logic module: the emotion enum, the motion-event JSON builder, and a streaming `EmotionTagStripper`, with thorough tests.
- **How:** In `motion_events.py`: `EMOTIONS = ("neutral","happy","sad","surprised","thinking")`; `DEFAULT_EMOTION="neutral"`; a compiled regex `re.compile(r"\[emotion:\s*([a-zA-Z]+)\s*\]", re.IGNORECASE)`; `normalize_emotion(name) -> str` (lowercase, return it if in EMOTIONS else `neutral`). A class `EmotionTagStripper` with `feed(text: str) -> str` and `flush() -> str`: maintain an internal buffer; on `feed`, append, then repeatedly extract complete tags (record each via a callback or an internal `emotions` list using `normalize_emotion`), and emit all text up to the last point that cannot be the start of a partial tag — i.e. hold back a suffix only if it is a prefix of `[emotion:` … or an unterminated `[emotion:...`. Keep the held-back tail in the buffer; `flush()` returns whatever remains (a dangling `[` that never completed is emitted verbatim). Expose the parsed emotions (e.g. an `on_emotion` callback passed to the constructor, or a drained `pop_emotions()` list). `motion_event_json(state: str, emotion: str) -> bytes`: `json.dumps({"type":"motion","state":state,"emotion":normalize_emotion(emotion)}).encode()`. In `tests/test_motion_events.py` (pytest, mirroring `tests/test_config.py` style) cover DD-6's cases; assert no emitted text ever contains the substring `[emotion`.
- **Context:** `infra/pi/agent/hermes_tasks.py:32` (`UI_TOPIC`), `infra/pi/agent/tests/test_config.py` (test style), the enum in DD-2.
- **Verify:** `cd infra/pi/agent && .venv/bin/python -m pytest tests/test_motion_events.py -q` — green.

### Task 2: SOUL.md — emotion-tag instructions

- **Files:** `infra/pi/agent/SOUL.md` (append a section)
- **Depends on:** none
- **Scope:** S
- **What:** Instruct the LLM to emit inline `[emotion:xxx]` tags from the fixed enum.
- **How:** Append a Russian section (matching SOUL's voice) explaining: emit an inline tag `[emotion:<one of neutral|happy|sad|surprised|thinking>]` at the start of a reply and whenever the emotion shifts; the tag is removed before speech and chat (the user never sees/hears it); use the EXACT enum words; default to leaving it off / `neutral` when flat. Give one short example line. Keep it concise; do not disturb existing personality content.
- **Context:** `infra/pi/agent/SOUL.md` (tail — append after the existing sections), the enum in DD-2 (must match `motion_events.py` exactly).
- **Verify:** `grep -n "emotion:" infra/pi/agent/SOUL.md` shows the instruction + all five enum words present.

### Task 3: Frontend — consume motion events with precedence

- **Files:** `infra/pi/web/static/js/motion.js` (edit), `infra/pi/web/static/js/ops.js` (edit), `infra/pi/web/static/js/main.js` (edit), `infra/pi/web/static/js/motion.test.mjs` (extend)
- **Depends on:** none
- **Scope:** M
- **What:** Add `applyMotionEvent`, precedence over `lk.agent.state`, the emotion→expression map, the `ops.js` `motion` branch, and `main.js` wiring; extend the unit test.
- **How:** In `motion.js`: add `const EMOTION_EXPR = { neutral:'Normal', happy:'Smile', sad:'Sad', surprised:'Surprised', thinking:'Blushing' };`; add `let motionEventActive = false;`; in `setState`, `if (motionEventActive) return;` before the debounce check; add `applyMotionEvent(evt)` that takes `{state, emotion}`, resolves the state via the existing `resolve()`, sets `motionEventActive = true`, updates `current`, calls `avatar.playMotion(group,index)` and `avatar.setExpression(EMOTION_EXPR[String(emotion).toLowerCase()] || 'Normal')`; return `{ setState, applyMotionEvent }`. In `ops.js`: change the factory to `createOps(opsEl, toolfeedEl, { log, onMotion })`; in the `DataReceived` dispatch add `else if (evt.type === 'motion') { if (onMotion) onMotion(evt); }`. In `main.js`: pass `onMotion: (evt) => motion.applyMotionEvent(evt)` into `createOps(...)` (motion is already constructed before ops — verify order; if not, reorder so `motion` exists first). In `motion.test.mjs`: add assertions — `applyMotionEvent({state:'speaking',emotion:'happy'})` → `playMotion('TapBody',2)` + `setExpression('Smile')`; unknown emotion → `Normal`; after one `applyMotionEvent`, a subsequent `setState('listening')` is ignored (no new avatar calls).
- **Context:** the slice-2 `motion.js` (table/`resolve`/`current`), `ops.js` `DataReceived` handler (topic `voiceagent`, type dispatch), `main.js` controller instantiation + `createOps` call, `avatar.js` `setExpression` + Natori names (`Normal/Smile/Sad/Surprised/Blushing`).
- **Verify:** `node --check` on motion.js/ops.js/main.js; `node infra/pi/web/static/js/motion.test.mjs` — all assertions pass.

### Task 4: Wire emotion stripping + motion publishing into agent.py

- **Files:** `infra/pi/agent/agent.py` (edit)
- **Depends on:** Task 1
- **Scope:** M
- **What:** Override `llm_node` to strip tags + publish emotion-driven motion events, and publish state-driven motion events on `agent_state_changed`.
- **How:** Import from `motion_events` (`EmotionTagStripper`, `motion_event_json`, `DEFAULT_EMOTION`). Give `GreetingAgent` a `publish_motion` callback (constructor arg) and a `current_emotion` attribute (init `DEFAULT_EMOTION`). Override `async def llm_node(self, chat_ctx, tools, model_settings)`: reset `self.current_emotion = DEFAULT_EMOTION`; build an `EmotionTagStripper` with an `on_emotion` that sets `self.current_emotion` and calls `publish_motion(motion_event_json("speaking", emotion))`; iterate `super().llm_node(...)` (the default generator via `Agent.default.llm_node` or `super()`); for each item, if it is a `ChatChunk` with `delta` and `delta.content`, set `chunk.delta.content = stripper.feed(chunk.delta.content)` (skip yielding if the cleaned content is empty AND there is no tool call) and yield; otherwise yield unchanged; after the loop, emit `stripper.flush()` as a final `str` chunk if non-empty. In `entrypoint`: build `publish_motion = lambda payload: ctx.room.local_participant.publish_data(payload, reliable=True, topic=UI_TOPIC)`; pass it to `GreetingAgent`; register `@session.on("agent_state_changed")` to `publish_motion(motion_event_json(ev.new_state, agent_ref.current_emotion))` (hold an `agent` reference; the session `say` greeting path is unaffected). Keep tool-call chunks intact. Match the file's existing comment density and style.
- **Context:** `infra/pi/agent/agent.py:67-84` (GreetingAgent), `:160-219` (entrypoint session setup + `publish_data` pattern at `:169-173`), `:226-233` (existing `session.on` hook style), `llm/llm.py:71-81` (`ChoiceDelta.content`), `voice/events.py:307-310` (`AgentStateChangedEvent.new_state`), Task 1's `motion_events` API.
- **Verify:** `python3 -m py_compile infra/pi/agent/agent.py`; `cd infra/pi/agent && .venv/bin/python -m pytest tests/ -q` — green (no regressions).

### Task 5: Validation

- **Files:** —
- **Depends on:** all
- **Scope:** S
- **What:** Full validation of the changed surface.
- **How:** Run the agent test suite + py_compile + the frontend syntax/test.
- **Context:** —
- **Verify:** `cd infra/pi/agent && .venv/bin/python -m pytest tests/ -q` — green; `python3 -m py_compile infra/pi/agent/agent.py infra/pi/agent/motion_events.py`; `for f in infra/pi/web/static/js/*.js; do node --check "$f"; done`; `node infra/pi/web/static/js/motion.test.mjs` — pass. End-to-end (a tagged reply → clean audio/transcript + matching expression) is DEFERRED until the LLM quota resets — note in the report.

## Execution

- **Mode:** sub-agents
- **Parallel:** true
- **Reasoning:** Tasks 1 (agent module), 2 (SOUL.md), 3 (frontend) touch disjoint files and run together; Task 4 wires the agent and depends on Task 1's module; Task 5 validates.
- **Order:**
  Group 1 (parallel): Task 1, Task 2, Task 3
  ─── barrier ───
  Group 2 (sequential): Task 4
  ─── barrier ───
  Group 3 (sequential): Task 5

## Verification

From issue #28 acceptance criteria:

- Agent Worker publishes `{type:"motion"}` events on the `voiceagent` topic carrying motion state + expression.
- SOUL.md instructs the LLM to emit inline emotion tags from the fixed enum.
- The agent parses and strips emotion tags from the streamed response before TTS and before the transcript.
- Emotion enum (`neutral|happy|sad|surprised|thinking`) is the shared contract; unknown → `neutral`.
- Frontend applies motion events with precedence over `lk.agent.state` and maps each emotion to a `.exp3.json`.
- A tagged reply produces the matching expression + motion with clean audio/transcript (**deferred** — needs LLM).
- Tests cover tag parsing/stripping in the Agent Worker.

## Materials

- ADR-0009 `docs/adr/0009-agent-authoritative-motion-emotion.md`.
- `.yoke/context.md` — glossary (Motion event, Emotion tag, Expression, UI topic).
- Slice 2 (#27) frontend `motion.js`/`avatar.js` (Natori expressions `Normal/Smile/Sad/Surprised/Blushing`).
- livekit-agents: `Agent.llm_node` override, `ChatChunk.delta.content` (mutable), `session.on("agent_state_changed")` (`new_state`).
- **Constraint:** LLM quota (ollama.com weekly limit) is currently exhausted — end-to-end verification is deferred; all unit/syntax validation runs now.
