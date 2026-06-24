# Barge-in interruption (close-socket TTS abort) — implementation plan

**Task:** GitHub issue #14 (Epic 4 — Agent Worker on Pi 5)
**Complexity:** simple (verification + tests + docs; no production logic change required)
**Mode:** sub-agents
**Parallel:** true

## Context — barge-in is already provided by the framework

Verified against installed `livekit-agents==1.6.2` and our code. `AgentSession` enables interruptions by default and our `DesktopTTS` already opens a fresh `/tts` socket per utterance and closes it in a `finally`. The full barge-in chain works without new production code:

- **Trigger:** VAD speech ≥ `min_duration` (0.5s) while the agent speaks → the framework **pauses** playout immediately (`agent_activity.py:1781-1934`); on the user's **final transcript** it commits the interruption (`_cancel_speech_pause(interrupt=True)`), or resumes if the user falls silent within `false_interruption_timeout` (2.0s).
- **Abort:** a committed interruption runs `cancel_and_wait(_tts_inference_task)` → `StreamAdapter` exits → `ChunkedStream.aclose()` cancels our `_run` → `CancelledError` → `finally: await ws.close()` (`tts_plugin.py:106-107`) → the Desktop `/tts` server's producer-cancel fires (`infra/desktop/tts/server.py:100-118`).
- **No tail:** `audio_output.clear_buffer()` + RTC `clear_queue()` drop locally-buffered/queued frames on the committed interruption (`voice/generation.py:492`).
- **No leak:** `cancel_and_wait` awaits our `finally` close before returning; a 5s `INTERRUPTION_TIMEOUT` force-cancel is the backstop.

So this slice is **honest right-sizing**: make the behavior explicit + discoverable (a comment), prove the load-bearing abort-on-cancel with a unit test (the one real coverage gap), and document it. No server change, no agent logic change.

## Design decisions

### DD-1: No production logic change — rely on framework defaults

**Decision:** Do not add interruption code paths; barge-in is realized by the default `InterruptionOptions(enabled=True, min_duration=0.5, false_interruption_timeout=2.0)` plus our existing per-turn socket + `finally: ws.close()`.
**Rationale:** Verified — `AgentSession` (our `agent.py:108`, no interruption kwargs) already has interruptions ON (`voice/turn.py:188-196`); the cancel chain reaches our `_run`'s `finally` (`tts/stream_adapter.py` → `tts/tts.py:340-342` → `tts_plugin.py:106`). Adding code would duplicate framework behavior.
**Alternative:** Hand-rolled VAD-watch + manual `ws.close()` — rejected: reimplements `AgentSession` interruption (the ADR-0006 "hand-rolled orchestration loop" anti-option).

### DD-2: Make barge-in explicit with a comment, not redundant config

**Decision:** Add a short comment at the `AgentSession(...)` construction noting interruptions are on by default and pointing at the tuning surface (`turn_handling=TurnHandlingOptions(interruption=InterruptionOptions(...))`); do NOT pass the deprecated flat `allow_interruptions=`/`min_interruption_*` kwargs.
**Rationale:** Barge-in is "invisible" (handled by defaults), which is a maintenance trap — a comment makes it discoverable without churn. The flat kwargs are deprecated in 1.6.2 (`agent_session.py:212-222`); restating defaults via `TurnHandlingOptions` adds noise.
**Alternative:** Explicitly set `allow_interruptions=True` — rejected: deprecated kwarg, redundant with the default.

### DD-3: Cover the abort-on-cancel gap with an offline unit test

**Decision:** Add an observable `disconnected` event to `FakeTTSServer`, then unit-test that cancelling a `DesktopTTS` synthesis mid-stream closes the socket (server observes disconnect) and that rapid back-to-back cancels neither hang nor leak sockets.
**Rationale:** Existing tests cover fresh-socket-per-utterance (#3) and server-drop→client-raise, but NOT client-side mid-flight cancel — the exact barge-in abort path (#1/#5). The true VAD-driven e2e needs a live room and is the manual acceptance step.
**Alternative:** Only manual live testing — rejected: leaves the load-bearing abort path unguarded against regressions.

## Tasks

### Task 1: Observable disconnect in FakeTTSServer

- **Files:** `infra/pi/agent/tests/conftest.py` (edit)
- **Depends on:** none
- **Scope:** S
- **What:** Add a `disconnected` flag/`asyncio.Event` to `FakeTTSServer`, set when a client connection drops mid-stream (the handler catches `ConnectionClosed` / hits its `finally`).
- **How:** In the `FakeTTSServer` ws handler, wrap the send loop so a client disconnect sets `self.disconnected = True` (and/or an `asyncio.Event`) and records it. Do NOT break existing TTS fixtures/tests. Mirror the existing `connections` bookkeeping.
- **Context:** `infra/pi/agent/tests/conftest.py` (FakeTTSServer handler, `connections`, teardown idioms).
- **Verify:** `cd infra/pi/agent && .venv/bin/python -m pytest tests/ -q --co` collects clean; existing tests unaffected.

### Task 2: Barge-in abort-on-cancel tests

- **Files:** `infra/pi/agent/tests/test_tts_plugin.py` (edit)
- **Depends on:** Task 1
- **Scope:** M
- **What:** Add tests proving the interruption abort path on the TTS plugin.
- **How:** (a) `test_cancel_midstream_closes_socket`: start `DesktopTTS.synthesize()` against the fake server, consume the first frame, then cancel the stream (`await stream.aclose()` or cancel the consuming task); assert the fake server observed the disconnect (the Task-1 flag/event) and the client stopped consuming — i.e. closing the socket is what aborts. (b) `test_rapid_interruptions_no_leak`: run/cancel several synthesize cycles back-to-back on the same `DesktopTTS`; assert each used a fresh socket (`connections` increments), all are closed, and nothing hangs (bounded time). Mirror the existing async test idioms.
- **Context:** `infra/pi/agent/tests/test_tts_plugin.py` (existing cancel/disconnect tests + idioms), `infra/pi/agent/tts_plugin.py` (`ChunkedStream._run` try/finally, `aclose`), the Task-1 fixture.
- **Verify:** `cd infra/pi/agent && .venv/bin/python -m pytest tests/test_tts_plugin.py -q` — green; full suite no regression.

### Task 3: Document barge-in in agent.py (comment only)

- **Files:** `infra/pi/agent/agent.py` (edit)
- **Depends on:** none
- **Scope:** S
- **What:** Add a concise comment at the `AgentSession(...)` construction explaining barge-in is enabled by default (VAD pause → transcript-commit → TTS socket close → GPU producer cancel → buffered audio dropped) and where to tune it; update the module docstring Flow to mention interruption. No behavior change.
- **How:** Comment near the session ctor + one Flow bullet. Reference ADR-0006. Do not add kwargs.
- **Context:** `infra/pi/agent/agent.py` (AgentSession ctor + docstring), `docs/adr/0006-agent-turn-control.md`.
- **Verify:** `cd infra/pi/agent && .venv/bin/python -c "import agent"` imports; comment present; no logic diff (only comments/docstring).

### Task 4: Docs — README barge-in section

- **Files:** `infra/pi/agent/README.md` (edit)
- **Depends on:** Task 3
- **Scope:** M
- **What:** Document barge-in: how it works (pause-then-commit model, socket-close abort, GPU producer cancel, no audio tail), that it is on by default, the tuning knob, and the live smoke test (talk over the agent → it stops and takes the new turn). Reinforce the headphones caveat (with barge-in, the agent hearing its own TTS on speakers will self-interrupt).
- **How:** New "Barge-in / interruption" subsection mirroring the existing doc style; extend the smoke test.
- **Context:** `infra/pi/agent/README.md` (structure/tone), `docs/adr/0006-agent-turn-control.md`, `agent.py` (actual behavior).
- **Verify:** Manual read — behavior, tuning, smoke, headphones all present and accurate.

### Task 5: Validation

- **Files:** —
- **Depends on:** all
- **Scope:** S
- **What:** Run the full suite + import smoke; confirm acceptance criteria coverage (offline portions) and flag the live VAD-driven barge-in as the human-gated acceptance.
- **How:** `.venv/bin/python -m pytest tests/ -q`; `import agent`.
- **Verify:** suite green; import clean.

## Execution

- **Mode:** sub-agents
- **Parallel:** true
- **Reasoning:** T1, T3 touch disjoint files and run in parallel; T2 needs T1's fixture; T4 follows T3; T5 last.
- **Order:**
  Group 1 (parallel): Task 1, Task 3
  ─── barrier ───
  Group 2 (parallel): Task 2 (needs Task 1), Task 4 (needs Task 3)
  ─── barrier ───
  Group 3: Task 5

## Verification

Acceptance criteria (issue #14):

1. Speaking over the agent stops its speech promptly (TTS socket closes, GPU producer cancels) — DD-1 (framework pause→commit + our socket close + server cancel); proven by Task 2(a) at the plugin level; live acceptance for the full VAD path.
2. After an interruption the new user turn is handled (STT→LLM→TTS resumes) — DD-1 (framework); live acceptance.
3. Each agent turn uses a fresh `/tts` socket — already true (`tts_plugin.py:80`); covered by the existing `test_websocket_torn_down_per_utterance` + reaffirmed in Task 2.
4. No audio tail after interruption — DD-1 (`clear_buffer`/`clear_queue`); live acceptance (offline-unverifiable, framework-owned).
5. Rapid back-to-back interruptions don't wedge or leak — Task 2(b) stress test.

## Materials

- ADR-0006 — `docs/adr/0006-agent-turn-control.md` (interruption-by-socket-close; fresh TTS socket per turn).
- `infra/pi/agent/tts_plugin.py` (the abort mechanism), `infra/desktop/tts/server.py:100-118` (producer-cancel-on-disconnect, verified — no change).
- Installed refs: `voice/turn.py:188-196` (InterruptionOptions defaults), `voice/agent_activity.py:1781-1934` (pause/commit), `voice/generation.py:444-517` (clear_buffer + cancel).

## Human-gated acceptance (not a build blocker)

The true VAD-driven barge-in (talk over the agent in a live room → speech stops, new turn handled, no tail) needs the live SFU + STT/TTS + LLM + a real mic, like the #13 live test. All offline portions (abort-on-cancel, no-leak, fresh-socket) are unit-tested.
