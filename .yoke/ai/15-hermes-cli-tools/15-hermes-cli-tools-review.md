# Review: 15-hermes-cli-tools

**Source:** .yoke/ai/15-hermes-cli-tools/15-hermes-cli-tools-report.md (issue #15)
**Status:** ✅ pass with findings (all fixed)

## Findings

| Severity | Evidence | Acceptance impact | Recommendation | Fix status |
| -------- | -------- | ----------------- | -------------- | ---------- |
| Critical | CONTEXT.md L63 (Hands-and-Mouth Split) referenced `[[ask_hermes]]` and "the two [[Hermes Tools]]"; L79 (Hermes Session ID) said "every `ask_hermes` call" — both terms were replaced by `run_command` in the pivot. Dead wiki-links + wrong tool count. | Glossary accuracy (CONTEXT.md is a first-class artifact); a reader following the links hits non-existent terms | Rewrite both entries to reference `run_command` and the single whitelisted tool | ✅ Fixed |
| Important | `agent.py _load_text_file` caught only `FileNotFoundError`; a `PermissionError`/`IsADirectoryError`/`OSError` during startup raised an opaque traceback instead of the actionable fail-loud message SOUL uses | Startup robustness — a misconfigured *_PATH that exists but is unreadable gives a bad UX | Broaden the except to `(PermissionError, IsADirectoryError, OSError)` and raise a actionable RuntimeError | ✅ Fixed |
| Minor | The `mcp` pip package was left in the venv from the abandoned MCP exploration; unused by the final code | Venv hygiene; a fresh install wouldn't include it (requirements.txt untouched), but the running venv carried dead weight | `pip uninstall mcp` | ✅ Fixed |
| Minor | `DEFAULT_TIMEOUT_SECONDS = 120.0` was uncommented relative to Hermes's own `--max-turns` (default 90) | Future tuner clarity | Add a one-line comment relating the ceiling to Hermes's internal limit | ✅ Fixed |

## Fixed issues

| Finding | Files changed | Evidence |
| ------- | ------------- | -------- |
| Stale CONTEXT.md two-tool/ask_hermes references | CONTEXT.md | Hands-and-Mouth Split now says "via [[run_command]]" and "the single ... run_command tool"; Hermes Session ID now says "every `run_command(\"hermes chat ...\")` call (folded from stderr)"; `grep -c ask_hermes` = 0 |
| _load_text_file narrow except | infra/pi/agent/agent.py | new `except (PermissionError, IsADirectoryError, OSError)` raises actionable RuntimeError; +3 tests in test_soul.py cover unreadable file, directory path, optional-missing-returns-None |
| Leftover mcp package | venv | `pip uninstall -y mcp` removed mcp-1.28.0; `grep -rn "import mcp"` in our code = none; venv imports OK post-removal |
| Timeout comment | infra/pi/agent/worker_tools.py | 4-line comment added under DEFAULT_TIMEOUT_SECONDS relating 120s ceiling to Hermes max_turns=90 |

## Skipped issues

| Finding | Reason |
| ------- | ------ |
| — | none — all four findings fixed per user choice "Fix all findings" |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `.venv/bin/python -m pytest -q` | 43 passed, 1 warning | +3 new hardening tests (unreadable file, directory path, optional-missing); warning is the pre-existing silero deprecation (unrelated) |
| live `run_command("hermes chat -q 'say just OK' ...")` | `OK` + `session_id` | whitelisted exec still works after all fixes |
| live `run_command("cat /etc/passwd")` | rejected | whitelist still blocks non-hermes |
| `grep -c "ask_hermes\|hermes_sessions_list" CONTEXT.md` | 0 | no stale two-tool references remain |
| `_load_text_file` on unreadable file | actionable RuntimeError("could not be read") | verified live |
| `_load_text_file` on directory path | actionable RuntimeError("could not be read") | verified live |
| venv imports after `pip uninstall mcp` | OK | worker_tools, agent, config import cleanly |

## Recommendations

- Commit boundary: `/skill:gca #15` (all review fixes are in).
- Follow-up (HITL, noted in the do-report): a manual end-to-end smoke — join a LiveKit room, speak a request that the LLM delegates to Hermes via `run_command`, confirm the spoken reply is re-voiced (not Hermes raw output) and that a second delegated turn resumes the Hermes `session_id`. The unit tests cover `run_command` exhaustively against a fake subprocess; the live smoke covers the real `hermes` binary; the only path not exercised here is the full LLM tool-calling turn through the SFU.
- Optional later chore: the `livekit-agents[mcp]` install pulled a few extra transitive deps (httpx-sse, sse-starlette, starlette, uvicorn, pydantic-settings) into the venv beyond `mcp` itself. They're unused by the final code but harmless; a clean venv rebuild from `requirements.txt` (untouched) would drop them. Not worth a forced cleanup now.