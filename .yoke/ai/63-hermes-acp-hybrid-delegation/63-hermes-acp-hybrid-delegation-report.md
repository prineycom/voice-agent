# Report: 63-hermes-acp-hybrid-delegation

**Plan:** .yoke/ai/63-hermes-acp-hybrid-delegation/63-hermes-acp-hybrid-delegation-plan.md
**Mode:** sub-agents
**Status:** ✅ complete

Branch: `epic/hermes-acp-delegation` (created per confirmation gate; `main` reset to
pre-plan `05a3363`). Pre-existing unrelated wake-word WIP committed separately as
`01bf491` before execution, per the gate decision.

## Tasks

| #   | Task                                              | Status  | Commit    | Concerns |
| --- | ------------------------------------------------- | ------- | --------- | -------- |
| 1   | ACP ndjson JSON-RPC transport codec               | ✅ DONE | `55bbea9` + fix `24fa72b` | review fix: writer close after reader EOF |
| 2   | Fake `hermes acp` subprocess harness              | ✅ DONE | `9f8fe99` | — |
| 3   | AcpClient supervisor + initialize + respawn       | ✅ DONE | `646ecb3` | minor: double-`start()` would leak a proc (single-caller by design) |
| 4   | ACP sessions + streaming prompt + permission      | ✅ DONE | `35f607a` + fix `d89ac68` | review fix: reject concurrent prompt on same session |
| 5   | Config knobs                                      | ✅ DONE | `d7623b7` + comment fix `f126a56` | — |
| 6   | Manager skeleton — live state, UI feed            | ✅ DONE | `530da2f` | minor: cancel() count includes tasks that settled during cancel |
| 7   | Hybrid 8 s fast-window race                       | ✅ DONE | `44ed94d` + fix `4d08963` | review fix (Critical): window-state cleanup on cancellation |
| 8   | Milestone narration                               | ✅ DONE | `656359d` | — |
| 9   | Synthetic tool-turn reintegration                 | ✅ DONE | `03c09c3` + fix `af6623d` | review fix: chat_ctx lock + shutdown finalizer drain |
| 10  | Bounded delivery (window + soft barge-in)         | ✅ DONE | `f5dbd0d` | — |
| 11  | Failure/timeout honest reporting (300 s)          | ✅ DONE | `1ba957b` | — |
| 12  | Narrow `run_command` + docstring doctrine         | ✅ DONE | `80a1e75` | — |
| 13  | New tool adapters + make_hermes_manager wiring    | ✅ DONE | `24e1a65` | — |
| 14  | agent.py eager ACP spawn + wiring                 | ✅ DONE | `2c3192e` | — |
| 15  | Rewrite skills/hermes.md                          | ✅ DONE | `fe5ea57` | intentionally docs-ahead-of-code within the epic branch (converged by T13/T14) |
| 16  | Update docs/architecture/overview.md              | ✅ DONE | `052ab0a` | minor: outer diagram box wording |
| 17  | CLAUDE.md note + commit ADR-0007/0022             | ✅ DONE | `186e81c` | — |
| 18  | Validation                                        | ✅ DONE | — (validator run) | — |

Every task passed the executor → reviewer loop; five tasks needed one fix iteration
each, all re-reviewed and approved. Zero BLOCKED/SKIPPED.

## Post-implementation

| Step          | Status     | Commit |
| ------------- | ---------- | ------ |
| Validate      | ✅ pass    | NO_CHANGES |
| Documentation | ⏭️ skipped (no --update-docs; docs were in-plan tasks 15–17) | — |
| Format        | ✅ no_formatter (project configures none — nothing imposed) | NO_CHANGES |

## Validation

- lint: skip — no lint config in the project
- type-check: skip — no mypy/pyright config; mypy not installed
- test: ✅ `cd infra/pi/agent && .venv/bin/python -m pytest tests/ -q` — **243 passed** (1 pre-existing silero deprecation warning)
- build: N/A (no build step)

## Acceptance criteria coverage (issue #63)

- Quick ask (<8 s) returns synchronously, voiced same turn — T7 fast-window race + tests.
- Slow ask acks immediately, bounded delivery, no lost result — T7/T10 (window + 15 s fallback + soft barge-in; reintegration always precedes delivery; shutdown drains finalizers).
- Live milestones + "как там?" from live state, never stale — T8 narration + T6 live `_tasks` as single source of truth.
- Repeat/act from context, no re-delegation — T9 synthetic `task_result` FunctionCall+FunctionCallOutput via `ChatContext.insert`, serialized by a chat_ctx lock.
- Quick question during a long task not blocked — concurrent ACP sessions (T4, PoC-validated interleaving; max_concurrent=3).
- `run_command` literal-shell-only, free-form → `delegate` — T12/T13/T15 (docstrings + skill contract).
- Failures/timeouts honest, Hermes-down never a crash — T3 (non-fatal start, respawn), T11 (300 s cap, no auto-retry), DD-5.
- Agent test suite green — 243 passed (was 190 at baseline; +53 net new tests).

## Concerns

### Non-blocking review notes (recorded, not fixed)
- T3: calling `AcpClient.start()` twice would leak the first process — single-caller by design (only `_start_hermes` calls it once).
- T6: `cancel()`'s aggregate count can include a task that settled done/failed during the cancel await window (cosmetic speech inaccuracy).
- T16: architecture diagram's outer "Hermes CLI" box wording could align better with the "long-lived, supervised" framing.

### Repo hygiene incident (NOT from this epic — needs your attention)
During execution, an **orphaned merge-conflict index entry** appeared on
`infra/desktop/stt/tests/test_server.py` (3 stages, conflict markers in the worktree,
no MERGE_HEAD/REBASE_HEAD) — likely from a concurrent session on this shared checkout
(there is a matching `epic-2-3 extra STT lifespan/cuda tests (WIP)` stash). It blocked
all commits. It was preserved **losslessly** and the path reset to HEAD:
backup at `/tmp/claude-1000/-home-priney-repos-voice-agent/1ed671be-8474-47ea-b051-c2d9986cf1c2/scratchpad/orphaned-conflict-backup/`
(`test_server.py.worktree-with-markers`, `stage2-ours.py`, `stage3-theirs.py`,
`index-stages.txt`). To restore the conflict state exactly:
`git update-index --index-info < index-stages.txt && cp test_server.py.worktree-with-markers infra/desktop/stt/tests/test_server.py`.

### Design assumptions to re-verify on the live Pi (from the plan)
1. Final answer arrives as the `session/prompt` RPC response (chunk-accumulation
   fallback is implemented) — validate against live `hermes acp` 0.18.2.
2. `mcpServers: []` suffices with `--accept-hooks`.
3. Exact `kind`/`title` event vocabulary beyond `execute`/`terminal:` — unknown kinds
   fall back to a generic narration phrase.

## Changes summary

| File | Action | Description |
| ---- | ------ | ----------- |
| infra/pi/agent/acp_client.py | created | AcpConnection ndjson JSON-RPC codec + AcpClient supervisor (spawn/initialize/respawn, sessions, streaming prompt, permission auto-answer) |
| infra/pi/agent/hermes_tasks.py | rewritten | ACP task manager: live per-task state, fast-window race, narration, synthetic tool-turn reintegration (locked), bounded delivery, 300 s honest failure |
| infra/pi/agent/worker_tools.py | rewritten | `delegate`/`list_tasks`/`cancel` tools; `run_command` literal-shell-only; `make_hermes_manager(acp_client)` |
| infra/pi/agent/agent.py | modified | eager ACP spawn via `_start_hermes()`; renamed tool imports/registration (wake-word WIP blocks preserved untouched) |
| infra/pi/agent/config.yaml | modified | fast_window_s / max_concurrent / task_timeout_s / delivery_fallback_s / max_queued / output_limit_chars |
| infra/pi/agent/skills/hermes.md | rewritten | LLM contract: delegate vs run_command split, task_result semantics |
| infra/pi/agent/tests/conftest.py | modified | FakeAcpProc scripted ndjson peer + fake_acp_exec + ADR-shaped event builders |
| infra/pi/agent/tests/test_acp_client.py | created | 23 codec/supervisor/session tests |
| infra/pi/agent/tests/test_fake_acp.py | created | harness self-tests |
| infra/pi/agent/tests/test_hermes_tasks.py | rewritten | 44 manager tests (state, window, narration, reintegration, delivery, failure, adapters) |
| infra/pi/agent/tests/test_agent.py | created | tool registration + startup resilience |
| docs/architecture/overview.md | modified | ACP delegation model throughout |
| docs/adr/0022-hermes-acp-hybrid-delegation.md | committed | source-of-truth ADR (was untracked) |
| docs/adr/0007-hermes-cli-delegation.md | committed | superseded pointer (was uncommitted) |
| CLAUDE.md | modified | non-obvious ACP delegation bullet |

## Commits (chronological)

- `b740d10` #63 docs: add implementation plan
- `01bf491` feat(wake-word-activation): manual wake button + user-speaking probe (pre-existing WIP, separated)
- `d7623b7` #63 chore: ACP delegation config knobs
- `186e81c` #63 docs: ADR-0022 + supersede ADR-0007 + CLAUDE.md
- `80a1e75` #63 refactor: narrow run_command contract
- `fe5ea57` #63 docs: rewrite hermes skill contract
- `052ab0a` #63 docs: architecture overview
- `55bbea9` #63 feat: ACP transport codec
- `24fa72b` #63 fix: close writer on aclose after reader EOF
- `9f8fe99` #63 test: fake hermes acp harness
- `646ecb3` #63 feat: AcpClient supervisor
- `35f607a` #63 feat: ACP sessions + streaming prompt
- `d89ac68` #63 fix: reject concurrent prompt on same session
- `530da2f` #63 feat: task manager live state
- `44ed94d` #63 feat: hybrid 8s fast-window race
- `4d08963` #63 fix: fast-window cancellation cleanup
- `656359d` #63 feat: milestone narration
- `03c09c3` #63 feat: synthetic tool-turn reintegration
- `af6623d` #63 fix: serialize chat_ctx + drain finalizers
- `f5dbd0d` #63 feat: bounded delivery with soft barge-in
- `1ba957b` #63 feat: task timeout + honest failure
- `24e1a65` #63 feat: delegate/list_tasks/cancel tools
- `f126a56` #63 chore: fix stale tool name in config comments
- `2c3192e` #63 feat: eager ACP spawn at startup

## Next steps (not in scope)

- Deploy to the Pi and validate live against Hermes 0.18.2 (assumptions 1–3 above);
  the ADR notes the event shapes were PoC-validated on 0.18.2 and must be re-checked
  on Hermes upgrades.
- Resolve the orphaned STT-test conflict deliberately (see Concerns).
- Merge via PR (`epic/hermes-acp-delegation` → `main`), wake-word precedent PR #61.
