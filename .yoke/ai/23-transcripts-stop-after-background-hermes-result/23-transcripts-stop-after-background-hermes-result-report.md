# Report: 23-transcripts-stop-after-background-hermes-result

**Plan:** .yoke/ai/23-transcripts-stop-after-background-hermes-result/23-transcripts-stop-after-background-hermes-result-plan.md
**Mode:** sub-agents
**Status:** ✅ complete

## Tasks

| #   | Task                                                | Status  | Commit    | Concerns |
| --- | --------------------------------------------------- | ------- | --------- | -------- |
| 1   | Harden delivery worker (idle primitive + no swallow) | ✅ DONE | `eac52b1` | —        |
| 2   | Env-gate transcript diagnostics (AGENT_DIAG)        | ✅ DONE | `59b6b2a` | —        |
| 3   | Regression tests (idle gating + non-swallowed errors) | ✅ DONE | `62f0f5d` | —        |
| 4   | Validation                                          | ✅ DONE | —         | —        |

## Post-implementation

| Step          | Status                | Commit |
| ------------- | --------------------- | ------ |
| Validate      | ✅ pass               | —      |
| Documentation | ⏭️ skipped (no --update-docs) | — |
| Format        | ⏭️ N/A (no formatter configured) | — |

## Root cause

The proactive delivery worker injected an out-of-band `session.generate_reply()` using a
hand-rolled idle gate (`_session_is_idle`, loose `getattr` snapshots) that does not wait for
the framework's user-turn/EOU finalization. The framework's own `AgentSession.wait_for_idle()`
(`agent_session.py:1366` → `agent_activity.py:1569-1583`) waits for exactly those signals.
Firing into that finalization gap, combined with `contextlib.suppress(Exception)` swallowing
any resulting error around `await handle`, wedged the activity's turn + native transcription
forwarding (both user STT and agent transcripts render from LiveKit's `TranscriptionReceived`,
not the repo's `voiceagent` data channel) while the lower-level audio pipeline kept running.

## Fix

- `_wait_until_idle` now prefers `await self._session.wait_for_idle()` when the session
  exposes it, re-raising `CancelledError` and treating other exceptions as "stop this cycle";
  falls back to the original `_session_is_idle` poll when absent (keeps unit tests green).
- `_delivery_worker` calls `generate_reply(..., allow_interruptions=True)` (preserves barge-in,
  ADR-0006) and replaces `contextlib.suppress(Exception)` around `await handle` with an
  explicit try/except that re-raises cancellation and `log.warning`s real errors.
- Transcript diagnostics in `agent.py` are gated behind `AGENT_DIAG` (default off) instead of
  always-on; `agent_state_changed` dropped, `conversation_item_added` + `close` kept under the
  gate. `TODO(#23)` to delete after live confirmation.

## Validation

`cd infra/pi/agent && .venv/bin/python -m pytest tests/ -q` ✅ (62 passed, 0 failed)
`python -m py_compile agent.py hermes_tasks.py` ✅
Lint / type-check / build — N/A (no ruff/black/flake8/mypy configured in this project)

## Live-confirmation caveat

Acceptance #1 (subsequent user/agent turns keep transcribing after a spoken Hermes result)
cannot be reproduced in CI — `FakeSession` cannot model the real SpeechHandle /
transcription-sync wedge. Final confirmation needs one live run on the Pi 5 + Desktop GPU,
ideally with `AGENT_DIAG=1`. Acceptance #2 (remove diagnostics): satisfied in intent for now
(silent by default); fully delete the gated diagnostics in a follow-up once the live run
confirms the wedge is gone.

## Changes summary

| File                                       | Action   | Description                                                        |
| ------------------------------------------ | -------- | ----------------------------------------------------------------- |
| infra/pi/agent/hermes_tasks.py             | modified | Use framework `wait_for_idle`; stop swallowing handle errors      |
| infra/pi/agent/agent.py                    | modified | Env-gate transcript diagnostics behind `AGENT_DIAG`               |
| infra/pi/agent/tests/test_hermes_tasks.py  | modified | Regression tests for idle gating + non-swallowed delivery errors  |

## Commits

- `7ba1ab7` #23 docs: add implementation plan
- `eac52b1` #23 fix: gate proactive delivery on framework idle, stop swallowing handle errors
- `59b6b2a` #23 fix: env-gate transcript diagnostics behind AGENT_DIAG
- `62f0f5d` #23 test: cover idle-primitive gating and non-swallowed delivery errors
