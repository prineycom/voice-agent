---
tags:
  - voice-agent
  - adr
status: accepted
---

# Hermes Integration: Direct CLI Subprocess via Whitelisted run_command Tool

The Agent Worker reaches Hermes by shelling out to the `hermes` CLI as an async
subprocess, exposed to the LLM as ONE generic `livekit.agents.function_tool`,
`run_command(args: str)`. A whitelist of allowed command names lives in
`infra/pi/agent/config.yaml` (`worker_tools.allowed_commands`, default
`["hermes"]`) and is enforced by checking `shlex.split(args)[0]`. A skill file
(`infra/pi/agent/skills/hermes.md`) teaches the LLM the command patterns and is
appended verbatim to the Agent instructions alongside SOUL.md at startup.

This replaces an earlier grilling decision to use `hermes mcp serve` as an MCP
server with an events_wait long-poll and a detached background re-voicing task.
Probing the real `hermes mcp serve` showed it is a **messaging bridge** to
external chat platforms (Telegram/Discord/Slack), not a "delegate a task to
Hermes's brain" RPC — there is no tool that asks Hermes to reason on request.
The synchronous `hermes chat -q ... -Q --yolo --source tool` CLI was verified
on the Pi 5 to create files, return the answer + a `session_id`, and resume
context via `--resume <session_id>`. The standard function-calling flow
(tool returns the Hermes reply → LLM re-voices it in the same turn → TTS
speaks it) already honors the hands-and-mouth split and barge-in, with no
background scheduler needed.

## Considered Options

- **Direct `hermes` CLI subprocess + whitelisted `run_command` tool + skill** ✅ —
  uses the verified CLI; one generic tool covers all current and future `hermes`
  subcommands (new subcommand = add a skill pattern, no code change); whitelist
  is the safety boundary; standard function-calling re-voices in-turn.
- **`hermes mcp serve` MCP client (livekit-agents MCPServerStdio)** ❌ — the
  real `hermes mcp serve` exposes a messaging bridge (`messages_send` targets
  `telegram:…`, `conversations_list` returns Telegram threads), not a
  delegate-task-to-Hermes RPC; no "ask Hermes to do X" tool exists.
- **Two hand-written function tools (`ask_hermes` + `hermes_sessions_list`)** ❌ —
  more code, hardcoded to two subcommands, requires a code change for every new
  Hermes subcommand. Subsumed by the generic whitelisted tool + skill.
- **Detached background re-voicing + `events_wait` long-poll** ❌ — only made
  sense when Hermes's reply arrived outside a turn (long-poll). The CLI returns
  the full reply within the tool call, so the standard same-turn re-voicing is
  strictly simpler and barge-in works natively.

## Consequences

- The worker spawns `hermes` as a child process per tool call (no long-lived
  daemon, no MCP). Hermes persists its own session/memory on disk; the worker
  passes `session_id` back via `--resume` for dialog continuity across turns.
- The whitelist gates which binaries the LLM may run, NOT their flags —
  operators must trust a whitelisted binary wholesale. Adding `git` or `docker`
  later is one config line + one skill pattern + a restart.
- Hermes's `session_id:` line is emitted on **stderr** in `-Q` mode; `run_command`
  folds any `session_id:` line from stderr into the returned output so the LLM
  can resume.
- Graceful degradation: a missing binary, non-zero exit, timeout, or rejection
  returns a clear error **string** (never raises) so the agent keeps talking
  instead of crashing the turn. Hermes being down is recoverable, not a
  startup gate (unlike STT/TTS).
- No MCP package needed in the worker venv; PyYAML (already a transitive dep
  of livekit-agents) reads config.yaml.