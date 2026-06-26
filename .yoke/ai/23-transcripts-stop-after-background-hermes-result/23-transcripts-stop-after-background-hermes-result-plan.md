# Transcripts stop after a background Hermes result is delivered — implementation plan

**Task:** https://github.com/prineycom/voice-agent/issues/23
**Complexity:** medium
**Mode:** sub-agents
**Parallel:** true

## Root-cause analysis (static)

The session is not closed (audio keeps flowing), so the `output.transcription = None`
close path (`agent_session.py:1013`) is ruled out. User STT transcript forwarding and
agent TTS transcript forwarding are independent tasks, so "both stop while audio flows"
points to a turn-level / activity-level wedge, not a single failed forwarder.

The hand-rolled idle gate `_session_is_idle` (`hermes_tasks.py:347-355`) reads three loose
`getattr` snapshots (`agent_state`, `user_state`, `current_speech`). `current_speech` is
cleared at the *end* of a speech, before the framework's post-turn finalization (item
commit + EOU + `_user_turn_completed_atask`) is guaranteed done. The framework's own idle
primitive `AgentSession.wait_for_idle()` (`agent_session.py:1366` → `agent_activity.py:1569-1583`)
waits for exactly those signals. So the worker can fire an out-of-band `generate_reply`
into the user-turn finalization gap. The resulting generation/turn-state error is then
swallowed by `contextlib.suppress(Exception)` around `await handle`
(`hermes_tasks.py:330-331`), leaving the activity's turn + transcription bookkeeping wedged
while the lower-level audio pipeline keeps running.

**Caveat:** static analysis cannot prove which trigger fires first (the race vs. a swallowed
generation error) without the live logs the diagnostics produce. The plan applies the two
independently-correct, low-risk fixes (use the framework idle primitive + stop swallowing
errors) and converts rather than deletes the diagnostics, so a live Pi 5 + Desktop run can
confirm before acceptance #2 is fully closed.

## Design decisions

### DD-1: Gate proactive delivery on `AgentSession.wait_for_idle()`, fall back to the poll

**Decision:** In `_wait_until_idle`, prefer `await self._session.wait_for_idle()` when the
bound session exposes it (`hasattr`); otherwise keep the existing `_session_is_idle` poll
loop. Guard exceptions (`ActivityClosedError`/`RuntimeError`) as "stop delivering this cycle".
**Rationale:** `wait_for_idle()` (`agent_session.py:1366`) waits for in-flight EOU and
`_user_turn_completed_atask` (`agent_activity.py:1569-1583`) — the finalization the getattr
poll races. The `hasattr` fallback keeps `FakeSession`-based unit tests green.
**Alternative:** private `_wait_for_idle_and_hold` — rejected: private API, and
`generate_reply` enqueues synchronously so the residual window is negligible.

### DD-2: Stop silently swallowing the awaited handle; log instead

**Decision:** Replace `with contextlib.suppress(Exception): await handle`
(`hermes_tasks.py:330-331`) with `try: await handle / except asyncio.CancelledError: raise /
except Exception: log.warning(...)`. Keep the worker alive on error but never silent.
**Rationale:** Surfaces the generation/turn error the diagnostics were chasing.
`suppress(Exception)` already lets `CancelledError` (BaseException) propagate; re-raising it
explicitly preserves shutdown cancellation semantics (matches `hermes_tasks.py:274-289`).
**Alternative:** re-raise on error to kill the worker — rejected: one failed delivery should
not strand later results.

### DD-3: Keep `generate_reply` (not `say`), pass `allow_interruptions` explicitly

**Decision:** Keep `self._session.generate_reply(instructions=..., allow_interruptions=True)`.
**Rationale:** Delivery needs the LLM to summarize raw Hermes output into a short spoken
sentence (`_build_delivery`, `hermes_tasks.py:357-378`); `session.say()` speaks literal text
and bypasses the LLM. Explicit `allow_interruptions=True` documents intent and preserves
barge-in (ADR-0006, `agent.py:180-191`); equals current default behavior.
**Alternative:** `say()` — rejected (no summarization). `allow_interruptions=False` —
rejected (breaks barge-in on a proactive turn).

### DD-4: Convert the diagnostics to one env-gated debug hook, do not delete yet

**Decision:** Collapse the three always-on hooks (`agent.py:234-249`) into a block registered
only when `AGENT_DIAG` is truthy (default off), keeping `conversation_item_added` + `close`.
Add a `TODO(#23)` to delete after live confirmation.
**Rationale:** Acceptance #2 wants diagnostics gone "once root-caused," but this run cannot
confirm on live hardware. Gating satisfies #2's intent (silent in normal operation) while
retaining a confirmation path.
**Alternative:** delete outright — rejected as premature (no live confirmation). Leave
always-on — rejected (fails #2's intent, noisy).

### DD-5: Add CI-friendly regression coverage at the manager level

**Decision:** Add tests: (a) when the session exposes async `wait_for_idle`, the worker
awaits it before `generate_reply`; (b) when `await handle` raises, the worker logs and still
delivers a subsequent result (no strand, no swallow-and-die).
**Rationale:** FakeSession can't reproduce the live transcription-sync wedge, but it can lock
the two behavioral guarantees the fix introduces.
**Alternative:** no tests — rejected; the suite would pass even if DD-1/DD-2 regressed.

## Tasks

### Task 1: Harden the proactive delivery worker (idle primitive + non-silent await)

- **Files:** `infra/pi/agent/hermes_tasks.py` (edit ~L318-355)
- **Depends on:** none
- **Scope:** M
- **What:** Implement DD-1, DD-2, DD-3 in `_delivery_worker` / `_wait_until_idle`.
- **How:**
  1. In `_wait_until_idle` (L342-345): if `self._session is not None and hasattr(self._session, "wait_for_idle")`, `await self._session.wait_for_idle()` inside a `try` that catches `Exception` and returns; else fall back to the existing `while not self._session_is_idle(): await asyncio.sleep(...)` loop. Keep `_session_is_idle` for the fallback path.
  2. In `_delivery_worker` (L329-331): keep `handle = self._session.generate_reply(instructions=instructions, allow_interruptions=True)`; replace `with contextlib.suppress(Exception): await handle` with `try: await handle / except asyncio.CancelledError: raise / except Exception as e: log.warning("hermes proactive delivery failed: %r", e)`.
- **Context:** `hermes_tasks.py:318-355`; framework refs `agent_session.py:1366-1389` (`wait_for_idle`), `1200-1264` (`generate_reply`); `agent_activity.py:1559-1583`; `speech_handle.py:156-189,276-281`; `hermes_tasks.py:274-289` (CancelledError convention to match).
- **Verify:** `cd infra/pi/agent && python -m pytest tests/test_hermes_tasks.py -q` — green; no `suppress(Exception)` around `await handle`.

### Task 2: Env-gate the transcript diagnostics in the entrypoint

- **Files:** `infra/pi/agent/agent.py` (edit ~L230-249)
- **Depends on:** none
- **Scope:** S
- **What:** Implement DD-4 — register the diagnostics only when `AGENT_DIAG` is truthy (default off), keep `conversation_item_added` + `close`, add a `TODO(#23)`.
- **How:** Read the flag once (`os.getenv("AGENT_DIAG")`) near the entrypoint top; wrap the `@session.on(...)` registrations in `if diag_enabled:`. Keep handlers exception-safe (getattr-with-default). Comment: "TODO(#23): remove once the transcript-wedge fix is confirmed on live hardware." Ensure `os` is imported.
- **Context:** `agent.py:222-264` (metrics hook style + diagnostics + `session.start`).
- **Verify:** `cd infra/pi/agent && python -c "import agent"` imports clean; diagnostics silent unless `AGENT_DIAG` set.

### Task 3: Regression tests for the hardened worker

- **Files:** `infra/pi/agent/tests/test_hermes_tasks.py` (edit)
- **Depends on:** Task 1
- **Scope:** M
- **What:** Implement DD-5.
- **How:**
  1. Add a `FakeSession` variant with an async `wait_for_idle()` that records it was awaited and flips to idle; assert the worker awaits it before appending to `replies`.
  2. Add a `RaisingHandle` whose await raises a plain `Exception`; drive two sequential results, assert the first logs (via `caplog`) without raising out of `join()`, and the second is still delivered.
  3. Keep the existing FakeSession (no `wait_for_idle`) to lock the fallback path.
- **Context:** `tests/test_hermes_tasks.py:18-45` (FakeHandle/FakeSession), `:88-107,196-237` (delivery + idle-gating tests to mirror); `tests/conftest.py` (sys.path + pytest-asyncio).
- **Verify:** `cd infra/pi/agent && python -m pytest tests/test_hermes_tasks.py -q` — new tests pass, file green.

### Task 4: Validation

- **Files:** —
- **Depends on:** all
- **Scope:** S
- **What:** Full regression + static sanity; record the live-confirmation caveat.
- **How:** `cd infra/pi/agent && python -m pytest tests/ -q`; `python -c "import agent"`; grep-confirm no `suppress(Exception)` wraps `await handle` and diagnostics are gated.
- **Verify:** Whole suite passes.

## Execution

- **Mode:** sub-agents
- **Parallel:** true
- **Reasoning:** Task 1 and Task 2 touch disjoint files and have no dependency, so they run in parallel; Task 3 needs Task 1's new contract; Task 4 validates all.
- **Order:**
  Group 1 (parallel): Task 1, Task 2
  ─── barrier ───
  Group 2 (sequential): Task 3
  ─── barrier ───
  Group 3 (sequential): Task 4

## Verification

1. After a background Hermes result is spoken, subsequent user and agent turns continue to
   appear in the transcript. **(Requires a live Pi 5 + Desktop GPU run with `AGENT_DIAG=1` to
   confirm; not reproducible in CI — FakeSession cannot model the real transcription-sync.)**
2. Temporary diagnostics removed once root-caused. **(This run gates them off by default;
   full deletion is deferred to a follow-up after the live-confirmation run.)**
3. `cd infra/pi/agent && python -m pytest tests/ -q` — all green, including the new
   regression tests.
