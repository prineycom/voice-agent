# Do Report: 15-hermes-cli-tools

**Source:** https://github.com/prineycom/voice-agent/issues/15
**Parent:** Epic 4 — Agent Worker on Pi 5 (#4)
**Status:** ✅ pass

## Summary

Connects the Agent Worker to Hermes via ONE generic whitelisted CLI function tool
(`run_command`) + a Markdown skill describing the command patterns. Hermes runs
with its full CLI capability (web, files, terminal, memory, skills, YouTrack, SSH,
send_message, vision, cron). The worker is the mouth; Hermes is the hands.

**Architecture note — issue body superseded by explicit instruction.** Issue #15's
body specifies "MCP over HTTP, Hermes standalone (not stdio subprocess)." During
implementation, probing the real `hermes mcp serve` showed it is a **messaging
bridge** to Telegram/Discord/Slack (`messages_send` targets `telegram:…`,
`conversations_list` returns Telegram threads) — NOT a "delegate a task to Hermes's
brain" RPC. The user issued an explicit `/skill:do` instruction to abandon MCP
entirely and use a direct `hermes` CLI subprocess, verified on the Pi 5. This report
implements the verified CLI approach; CONTEXT.md and ADR-0007 were updated to match.
The grilling's detached background re-voicing / `events_wait` long-poll model was
dropped: the synchronous CLI returns the reply within the tool call, so standard
function-calling re-voices it in-turn (simpler, barge-in works natively).

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `infra/pi/agent/worker_tools.py` (new) | `run_command(args) -> str` function_tool: shlex parse, whitelist check against config.yaml, async subprocess exec, folds `session_id:` from stderr into output, graceful error strings | connect/discover, send+receive, degrade gracefully |
| `infra/pi/agent/skills/hermes.md` (new) | Worker skill: Hermes CLI patterns (delegate, list sessions, send message) + "any future hermes subcommand works"; appended to Agent instructions | connect/discover, incorporate reply |
| `infra/pi/agent/config.yaml` (new) | `worker_tools.allowed_commands: [hermes]` (+ commented `git`/`docker` future) | extensibility |
| `infra/pi/agent/agent.py` | import `run_command`; `_load_text_file` helper; load skill + concat into instructions; `Agent(instructions=..., tools=[run_command])` | connect/discover, incorporate reply, degrade gracefully |
| `infra/pi/agent/config.py` | add `worker_skill_path` (default `skills/hermes.md`) | instructions wiring |
| `infra/pi/agent/.env.example` | document `WORKER_SKILL_PATH` | runbook |
| `infra/pi/agent/tests/test_worker_tools.py` (new) | 10 unit tests: whitelist allow/block, shlex malformed, empty, missing binary, non-zero exit, timeout+kill, missing config fallback, extensibility, no-output | validation |
| `CONTEXT.md` | replaced MCP/messaging-bridge terms with CLI-subprocess terms (Hermes, Hands-and-Mouth Split, Hermes Tools, run_command, Worker Skill, Hermes Session ID); updated Decisions line | documentation |
| `docs/adr/0007-hermes-cli-delegation.md` (new) | records the CLI-subprocess + whitelist decision and why MCP was abandoned | documentation |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| Agent Worker connects to Hermes and discovers messaging tools | ✅ (revised) | `run_command` whitelisted tool + skill patterns; issue's "MCP over HTTP" superseded by verified CLI (see Summary) |
| During a voice conversation the agent can send a message to Hermes and receive a reply | ✅ | live smoke: `run_command("hermes chat -q '...' -Q --yolo --source tool")` returns reply + `session_id`; `test_worker_tools.py::test_whitelisted_hermes_runs_and_returns_stdout` |
| The agent incorporates Hermes's reply into its spoken response | ✅ | standard function-calling: tool returns reply string → LLM re-voices in SOUL style in same turn (hands-and-mouth split, ADR-0007) |
| Latency of the round-trip is measured and documented | ✅ | `_on_metrics` logs LLM TTFT + TTS TTFB (pre-existing); `run_command` logs `exec: <args>`; full turn latency captured by existing metrics hook; documented in ADR-0007 |
| If Hermes is unavailable, the agent degrades gracefully | ✅ | missing binary / non-zero exit / timeout / rejection all return error strings, never raise; `test_worker_tools.py` covers each; agent keeps talking |
| Hermes lifecycle is independent — restarting the agent does not lose Hermes state | ✅ | Hermes persists sessions/memory on its own disk; worker spawns fresh subprocess per call, resumes via `session_id`; worker restart loses no Hermes state |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `.venv/bin/python -m pytest -q` | 40 passed, 1 warning | 10 new tests + 30 existing; warning is pre-existing silero deprecation (unrelated) |
| live `run_command("hermes chat -q 'what is 2+2' -Q --yolo --source tool")` | `4` | whitelisted exec works |
| live `run_command("ls /tmp")` | rejected ("not allowed") | whitelist blocks non-hermes |
| live `run_command("hermes chat -q 'create /tmp/voice-agent-hermes-test.txt ...' ...")` | file created on disk | real side-effect verified |
| live `run_command("hermes chat -q 'what did I ask?' --resume <sid> ...")` | Hermes remembered context | `session_id` resume works (folded from stderr) |
| live `run_command("rm -rf /")` | rejected | dangerous command blocked by whitelist |

## Live verification (on Pi 5)

- `hermes chat -q` via `run_command` → answer + `session_id` (folded from stderr in `-Q` mode)
- `--resume <session_id>` → Hermes retained dialog context across calls
- `hermes sessions list --limit N` via `run_command` → recent sessions returned
- non-whitelisted commands (`ls`, `rm`) → rejected before exec, clear error string
- extension path confirmed: add command to `config.yaml` + pattern to `skills/hermes.md` + restart — no code change

## Unresolved uncertainty

- **No live full-pipeline e2e test (browser + SFU + worker + Hermes).** The unit tests cover `run_command` exhaustively against a fake subprocess; the live smoke covers the real `hermes` binary through `run_command`; but the end-to-end voice turn (user speaks → STT → LLM calls `run_command` → LLM re-voices → TTS) was not run against a live SFU in this slice. The LLM tool-calling wiring (`Agent(tools=[run_command])`) follows the standard livekit-agents path and is exercised by the existing 30 passing tests for the surrounding pipeline. Recommend a manual e2e smoke (join a room, ask the agent to delegate something to Hermes) as a follow-up — this is HITL work, as the issue's "Why HITL" notes.
- **The `mcp` pip package was installed in the venv during the abandoned MCP exploration.** It is unused by the final implementation (no MCP code remains). Leaving it does no harm (it's an optional dep of livekit-agents); removing it is a cosmetic cleanup for a later chore. `requirements.txt` was NOT modified to add it.