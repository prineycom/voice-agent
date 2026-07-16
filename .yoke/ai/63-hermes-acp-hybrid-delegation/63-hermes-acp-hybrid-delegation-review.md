# Code Review: 63-hermes-acp-hybrid-delegation

## Summary

### Context and goal

Replaces the old `hermes chat` CLI delegation (ADR-0007) with a long-lived `hermes acp` streaming process (ADR-0022): a new `acp_client.py` supervises the process and speaks ndjson JSON-RPC, while a rewritten `hermes_tasks.py` runs a hybrid fast-window model — quick asks answer synchronously in-turn, slow asks background and later re-enter `chat_ctx` as a synthetic `task_result` tool turn with bounded proactive delivery.

### Key code areas for review

1. **`acp_client.py:AcpConnection`** — ndjson JSON-RPC codec; reader loop, pending-future lifecycle, honest teardown of pending calls.
2. **`acp_client.py:AcpClient._ensure/_spawn_locked/_monitor_proc`** — lock-guarded spawn/respawn/crash-detection; the single lifecycle seam.
3. **`acp_client.py:AcpClient.prompt/_run_prompt`** — per-session routing registered before the RPC fires; final-answer resolution.
4. **`hermes_tasks.py:delegate()`** — fast-window race with the `awaiting_sync`/`completion_pending` single-fire handshake and `asyncio.shield` under `wait_for`.
5. **`hermes_tasks.py:_run_task/_consume_prompt`** — one `task_timeout_s` deadline over both event-stream and result; failure/timeout convergence.
6. **`hermes_tasks.py:_reintegrate()`** — `chat_ctx.copy()` → `insert([FunctionCall, FunctionCallOutput])` → `update_chat_ctx` under `_chat_ctx_lock`.
7. **`hermes_tasks.py:_deliver/_wait_for_pause`** — bounded idle race + soft barge-in under `_delivery_lock`.
8. **`hermes_tasks.py:shutdown()`** — cancels runners/UI tasks but drains (not cancels) finalizers with a 2s timeout.
9. **`hermes_tasks.py:cancel/_cancel_task`** — cancellation settlement and minimal context record.
10. **`agent.py:_start_hermes()`** — eager, never-fatal ACP startup; preserved wake-word edits.

### Complex decisions

1. **Fast-window single-fire handshake** (`hermes_tasks.py`) — `awaiting_sync` defers completion side-effects into `completion_pending`, drained once in the `finally`. Verified single-fire across win / timeout / delegate-cancellation (barge-in).
2. **`asyncio.shield(task.first_result)` under `wait_for`** — a fast-window timeout must not cancel the underlying task; `first_result` is only ever `set_result` (never `set_exception`), so no "exception never retrieved". Correct.
3. **Answer extraction is heuristic** (`acp_client.py`) — the Hermes 0.18.2 `session/prompt` result shape is an ADR open point; falls back to accumulated `agent_message_chunk` text. Needs live validation.
4. **Two separate task sets** (`hermes_tasks.py`) — UI tasks are cancelled on shutdown; finalizers are drained, since cancelling one drops a finished task's context record.

### Questions for the reviewer

1. In `_reintegrate`, `chat_ctx.copy()` and `update_chat_ctx()` bracket an `await`, and `update_chat_ctx` **replaces** (not merges) the context. `_chat_ctx_lock` serializes finalizers against each other but not against the framework's own chat_ctx mutations. Can a live conversation turn landing in that window be silently dropped, or does the framework guard this? **Highest-value item to confirm on the live Pi** (fix mitigates the window; residual race documented in-code).
2. Has the `_extract_answer` key list (`text/answer/response/content/message`) been checked against a real `hermes acp` 0.18.2 `session/prompt` result, or does it currently rely entirely on the chunk-accumulation fallback?

### Risks and impact

- Answer extraction depends on an unconfirmed wire shape (ADR open point) — a schema mismatch silently degrades to concatenated stream chunks, which may be empty or partial.
- Chat_ctx replace-semantics race (Question 1) could drop a concurrent conversation turn; only reproducible under live load. Mitigated but not eliminated.
- UI datagram volume on tool-heavy tasks — addressed by snapshot coalescing (Issue #4 fix).

### Tests and manual checks

**Auto-tests:**

- 243 tests pass (`test_acp_client.py`, `test_hermes_tasks.py`, `test_agent.py`, `test_fake_acp.py`, `test_wake_state.py`, `test_worker_tools.py`).

**Manual scenarios:**

1. Deploy to Pi → validate the ADR non-blocking assumptions against real `hermes acp` 0.18.2: `session/prompt` result shape, `mcpServers=[]` sufficiency, `--accept-hooks` avoiding permission round-trips.
2. Long delegated task under concurrent speech → confirm no conversation turn is dropped during `_reintegrate` (chat_ctx race).

### Out of scope

- Frontend `room.js` / `main.js` / `face3d-main.js` changes (event-shape compatibility only).
- Docs (ADR-0022, overview.md, skills/hermes.md, CLAUDE.md).
- Wake-word manual-button and user-speaking-probe edits (from a prior epic, preserved untouched).

## Commits

| Hash    | Description                                                     |
| ------- | -------------------------------------------------------------- |
| 04e0b55 | #63 fix: fix 4 review issues                                   |
| acfbcc4 | #63 docs: add execution report                                 |
| 2c3192e | #63 feat: spawn ACP client eagerly at worker startup           |
| f126a56 | #63 chore: fix stale tool name in config comments              |
| 24e1a65 | #63 feat: replace hermes tools with delegate, list_tasks, cancel |
| 1ba957b | #63 feat: enforce task timeout with honest failure reporting   |
| f5dbd0d | #63 feat: bounded background report delivery with soft barge-in |
| af6623d | #63 fix: serialize chat_ctx reintegration and drain finalizers |
| 03c09c3 | #63 feat: reintegrate background results as synthetic tool turns |
| 656ed03 | #63 feat: narrate task milestones from ACP tool events         |
| 4d08963 | #63 fix: clean up fast-window state on cancellation            |
| 44ed94d | #63 feat: add hybrid 8s fast-window race to delegate           |
| 530da2f | #63 feat: rewrite task manager with ACP live per-task state    |
| d89ac68 | #63 fix: reject concurrent prompt on same ACP session          |
| 35f607a | #63 feat: add ACP sessions, streaming prompt, permission auto-answer |
| 646ecb3 | #63 feat: add AcpClient supervisor with initialize and respawn |
| 9f8fe99 | #63 test: add scripted fake hermes acp subprocess harness      |
| 24fa72b | #63 fix: close writer on aclose after reader EOF               |
| 55bbea9 | #63 feat: add ACP ndjson JSON-RPC transport codec              |
| 052ab0a | #63 docs: update architecture overview for ACP delegation      |
| fe5ea57 | #63 docs: rewrite hermes skill contract for delegate/run_command |
| 80a1e75 | #63 refactor: narrow run_command contract to literal shell only |
| 186e81c | #63 docs: add ADR-0022, supersede ADR-0007, note in CLAUDE.md  |
| d7623b7 | #63 chore: add ACP delegation config knobs                     |

## Changed Files

| File                                     | Description                                                          |
| ---------------------------------------- | ------------------------------------------------------------------- |
| infra/pi/agent/acp_client.py             | +887 — created: ndjson JSON-RPC codec + AcpClient supervisor        |
| infra/pi/agent/hermes_tasks.py           | rewritten — ACP task manager (fast-window, reintegration, delivery) |
| infra/pi/agent/worker_tools.py           | rewritten — delegate/list_tasks/cancel; run_command literal-shell   |
| infra/pi/agent/agent.py                  | +75/-… — eager ACP spawn via `_start_hermes()`; tool re-wiring      |
| infra/pi/agent/config.yaml               | ACP delegation knobs (fast_window_s, max_concurrent, timeouts)      |
| infra/pi/agent/wake_state.py             | +20 — wake-state helper (adjacent epic)                             |
| infra/pi/agent/skills/hermes.md          | rewritten — delegate vs run_command LLM contract                    |
| infra/pi/agent/tests/*                   | +3000 — codec/supervisor/manager/agent/worker test suites           |
| docs/adr/0022-…                          | +166 — source-of-truth ADR                                          |
| docs/architecture/overview.md            | ACP delegation model throughout                                     |
| CLAUDE.md, docs/adr/0007-…               | non-obvious bullet + superseded pointer                             |

## Issues Found

| Severity  | Score | Category      | File:line              | Description                                                                                   |
| --------- | ----- | ------------- | ---------------------- | --------------------------------------------------------------------------------------------- |
| Important | 48    | bugs          | hermes_tasks.py:763    | `_reintegrate` copy→`await update_chat_ctx` (replace) race not guarded vs framework mutations  |
| Minor     | 35    | documentation | worker_tools.py:188    | `run_command` still folds retired `session_id:`/`-Q`/`--resume` resume logic                   |
| Minor     | 30    | quality       | hermes_tasks.py:402    | Shutdown finalizer 2s drain vs `_deliver` 15s pause wait → false "context may be lost" warning |
| Minor     | 28    | performance   | hermes_tasks.py:615    | `_on_tool_event` emits a publish+triple-resend per ACP event → redundant datagram flood        |

## Fixed Issues

| Issue                                            | Commit    | Description                                                                                     |
| ------------------------------------------------ | --------- | ---------------------------------------------------------------------------------------------- |
| `_reintegrate` chat_ctx replace race             | `04e0b55` | Build synthetic turn outside the lock; fresh copy+insert+replace in one tight critical section; race documented in-code, flagged for live-Pi confirmation |
| `run_command` retired resume folding             | `04e0b55` | Removed `session_id:`/`-Q`/`--resume` folding + comment; returns plain stdout tail; test updated to literal-shell semantics |
| False "context may be lost" shutdown warning     | `04e0b55` | `_deliver` short-circuits when `self._closing` is set, so finalizers complete fast after reintegration |
| Redundant UI datagram flood                      | `04e0b55` | `_emit_tasks` coalesces via `_tasks_signature`; skips publish + resends when snapshot unchanged |

## Skipped Issues

> **All found issues were fixed.**

## Recommendations

- **Live-Pi validation is the top priority.** Two accepted-then-mitigated items hinge on unconfirmed Hermes 0.18.2 behavior: (1) the `_reintegrate` chat_ctx replace race — confirm whether livekit-agents `update_chat_ctx` merges or the framework quiesces during it, and adopt an in-place insert/append API if one exists; (2) the `_extract_answer` result-shape heuristic — verify against a real `session/prompt` response rather than relying on the chunk-accumulation fallback.
- Carry forward the report's non-blocking notes (double-`start()` proc leak — single-caller by design; `cancel()` aggregate count cosmetic drift; architecture-diagram wording) as accepted; no code change warranted.
- Resolve the orphaned STT-test merge-conflict incident (unrelated to this epic; backup preserved per the execution report) before merging.
- Merge via PR (`epic/hermes-acp-delegation` → `main`), following wake-word precedent PR #61.
