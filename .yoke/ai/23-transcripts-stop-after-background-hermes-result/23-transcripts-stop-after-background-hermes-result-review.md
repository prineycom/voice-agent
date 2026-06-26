# Code Review: 23-transcripts-stop-after-background-hermes-result

## Summary

### Context and goal

Fixes issue #23: the proactive Hermes delivery worker injected an out-of-band
`session.generate_reply()` behind a hand-rolled idle gate that raced the framework's
user-turn/EOU finalization, and swallowed handle errors via `contextlib.suppress(Exception)` —
wedging native LiveKit transcription forwarding (both user STT and agent lines). The change
prefers the framework's `session.wait_for_idle()` (with a poll fallback), passes
`allow_interruptions=True`, replaces the blanket suppress with a logging `try/except`, gates
diagnostics behind `AGENT_DIAG`, and adds two regression tests.

### Key code areas for review

1. **`hermes_tasks.py:_wait_until_idle()`** — framework-primitive-first idle gate; on a
   non-cancellation error it now falls through to the state poll (post-review fix).
2. **`hermes_tasks.py:_delivery_worker()`** — `allow_interruptions=True` plus the `try/except`
   that re-raises `CancelledError` and `log.warning`s other errors.
3. **`agent.py` entrypoint** — diagnostics moved behind `if diag_enabled:` (`AGENT_DIAG`).
4. **`tests/test_hermes_tasks.py`** — `IdlePrimitiveSession` (idle-before-reply ordering) and
   `FlakyHandleSession`/`RaisingHandle` (failed delivery logged, worker survives, next result
   still delivered).

### Complex decisions

1. **Prefer framework idle primitive with `hasattr` fallback** (`hermes_tasks.py:357`) —
   couples to a livekit-agents API; `hasattr` keeps pinned versions and unit tests working.
2. **`allow_interruptions=True` on proactive delivery** (`hermes_tasks.py:330`) — lets the
   user cut off a background result mid-sentence; deliberate, preserves barge-in (ADR-0006).
3. **Gate diagnostics, not delete** (`agent.py`) — acceptance #2 deferred until a live run
   confirms the wedge is gone (TODO(#23)).

### Questions for the reviewer

1. On a session mid-shutdown, `wait_for_idle()` raises `ActivityClosedError`; the worker now
   falls back to the poll, which returns idle and calls `generate_reply` on a closing session
   (caught/logged). Acceptable, or should a closing session short-circuit delivery?

### Risks and impact

- Behavior change is confined to the proactive-delivery path; request/queue/cancel logic is
  untouched.
- The transcript-wedge fix itself still needs real-hardware confirmation (accepted CI gap —
  `FakeSession` cannot model the real SpeechHandle/transcription-sync).

### Tests and manual checks

**Auto-tests:** `cd infra/pi/agent && .venv/bin/python -m pytest tests/ -q` → 62 passed.
The two new tests passed 5/5 consecutive runs with no flakiness.

**Manual scenarios:**
1. Trigger a Hermes delegation, let the agent speak the result → subsequent user and agent
   turns must continue to appear in the transcript panel (run with `AGENT_DIAG=1` to capture
   `conversation_item_added`/`close` diagnostics).

### Out of scope

- The accepted CI limitation and the deferred deletion of the `AGENT_DIAG`-gated diagnostics
  (TODO(#23)).

## Commits

| Hash    | Description                                                                  |
| ------- | --------------------------------------------------------------------------- |
| 7ba1ab7 | #23 docs: add implementation plan                                           |
| eac52b1 | #23 fix: gate proactive delivery on framework idle, stop swallowing errors  |
| 59b6b2a | #23 fix: env-gate transcript diagnostics behind AGENT_DIAG                  |
| 62f0f5d | #23 test: cover idle-primitive gating and non-swallowed delivery errors     |
| a076f31 | #23 docs: add execution report                                             |
| 3d4526c | #23 fix: fix 2 review issues                                               |

## Changed Files

| File                                       | +/-      | Description                                          |
| ------------------------------------------ | -------- | ---------------------------------------------------- |
| infra/pi/agent/hermes_tasks.py             | +27/-…   | Framework idle primitive + poll fallback; no swallow |
| infra/pi/agent/agent.py                    | +…/-…    | Env-gate transcript diagnostics behind AGENT_DIAG    |
| infra/pi/agent/tests/test_hermes_tasks.py  | +99      | Regression tests for idle gating + delivery errors   |

## Issues Found

| Severity  | Score | Category | File:line                    | Description                                                                                      |
| --------- | ----- | -------- | ---------------------------- | ----------------------------------------------------------------------------------------------- |
| Important | 55    | bugs     | `hermes_tasks.py:363-364`    | `except Exception: return` bypassed idle-gating and the poll fallback on non-cancellation errors |
| Minor     | 30    | quality  | `hermes_tasks.py:363`        | Error from `wait_for_idle()` was swallowed with no log line                                       |

## Fixed Issues

| Issue                                          | Commit    | Description                                                          |
| ---------------------------------------------- | --------- | ------------------------------------------------------------------ |
| Idle-gating bypassed on error (`:363-364`)     | `3d4526c` | Non-`CancelledError` now falls through to the state poll loop        |
| Swallowed error from `wait_for_idle` (`:363`)  | `3d4526c` | Bind `as e` + `log.debug(...)` before falling back                  |

## Skipped Issues

**All found issues were fixed.**

## Recommendations

- Confirm acceptance #1 on a live Pi 5 + Desktop GPU run with `AGENT_DIAG=1`: after a spoken
  Hermes result, verify subsequent user/agent turns keep transcribing. Then delete the
  `AGENT_DIAG`-gated diagnostics (TODO(#23)) to close acceptance #2.
- Consider whether a closing session should short-circuit proactive delivery rather than fall
  through to the poll (reviewer question above) — low priority, current behavior logs and is safe.
