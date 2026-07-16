---
tags:
  - voice-agent
  - adr
status: accepted
supersedes: 0007-hermes-cli-delegation
---

# Hermes Delegation v2: ACP Streaming Transport + Hybrid Fast-Window Delegation

The Agent Worker talks to Hermes over a **long-lived `hermes acp` process** (Agent
Client Protocol — JSON-RPC over stdio), **one ACP session per delegated task**,
replacing the per-call `hermes chat -q … -Q` subprocess of ADR-0007. A single
`delegate(request)` tool races the task against an **8 s fast window**: if Hermes
answers within 8 s the reply is returned **synchronously as the tool result**; if
not, the tool returns a `task_id` and the task continues **in the background**, its
result later re-entering the LLM conversation as a **synthetic tool turn** (a
paired `assistant{tool_call} + tool{result}`), not a floating `system` message.
Tool-progress events streamed by ACP drive **milestone narration** ("смотрю
YouTrack…"), and a single live per-task state feeds `list_tasks`. `run_command`
survives as a narrow **literal-shell-command** escape hatch only.

This supersedes ADR-0007 (and its 2026-06-25 async-delegation update) and the
`HermesTaskManager` fire-and-forget design in `docs/superpowers/specs/2026-06-25-
hermes-async-delegation-design.md`.

## Context

The 0007 design has two divergent Hermes paths bolted onto one CLI, and the LLM
picks between them badly. Four symptoms were reported from live sessions and traced
to code (`infra/pi/agent/worker_tools.py`, `hermes_tasks.py`, `agent.py`):

- **No completion report.** Background results are delivered only after
  `_wait_until_idle()` (`hermes_tasks.py`); in a live conversation that idle window
  may never arrive, so a finished result sits in `_pending` unspoken. A latent bug
  clears `_pending` **before** the `self._session is None` guard, silently dropping
  the batch.
- **No intermediate status.** `hermes chat` runs with `-Q` (quiet) + a single
  terminal `communicate()`, so Hermes's intermediate steps are structurally
  invisible. `list_hermes_tasks` knows only a label + running/queued.
- **"Task still running" after it finished.** On completion the task leaves
  `_labels` but the result waits in `_pending`; `list_tasks()` never inspects
  `_pending`, so the status source is out of sync with reality.
- **Re-queries instead of reading context.** The real answer is injected as a
  `role="system"` message, *not* a tool result tied to the original `tool_call_id`.
  The recorded tool result literally says "running in background"; the answer
  arrives as a loosely-associated system note the model does not treat as the
  answer — so it re-delegates. If injection is skipped/dropped, the answer exists
  only as spoken TTS and is never in history at all.

Root cause (one disease behind all four): **a background task's result never
becomes a first-class, context-persisted, tool-linked result, and there is no live
task state.** Secondary: a single shared `self._session_id` races across the 3
concurrent tasks on `--resume`.

Prober findings that reshaped the fix:

- `hermes chat` has **no** `--json/--stream/--events` mode. Non-`-Q` stdout is
  emoji/ANSI prose indexed by an ephemeral `Tool N` counter — not machine-parseable.
  `~/.hermes/logs/agent.log` has clean `tool <name> completed` lines but **only the
  completed edge**, correlated by session id (fragile log-scraping).
- **`hermes acp`** (the ACP adapter, `agent-client-protocol` lib) is a proper
  streaming JSON-RPC channel: `ToolCallStart` / `ToolCallUpdate` **and** the final
  result over one connection, with real session ids — no scraping, no race.

## Decision

Adopt the hybrid (variant C) design over ACP. Concrete shape:

**Transport.** Worker holds one long-lived `hermes acp --accept-hooks` process,
spawned **at worker startup** (not on first delegation — cold start is ~8 s to
`initialize`). ndjson JSON-RPC: `initialize {protocolVersion:1}` → `session/new
{cwd, mcpServers}` → `session/prompt {sessionId, prompt:[{type:text,…}]}`; the agent
consumes `session/update` notifications and defensively answers any
`session/request_permission` (with `--accept-hooks`, none were requested in the PoC).

**Tools (LLM-facing).** Contract split hard so the LLM never re-confuses paths (the
01-07 failure was `run_command` used for "найди мои задачи"):
- `delegate(request)` — any free-form intent/task → hybrid path below.
- `run_command(cmd)` — **only a literal shell command** (`git log`, `df -h`). Any
  free-form request ("найди…", "сделай…") is explicitly forbidden here, enforced in
  the docstring + `skills/hermes.md`.
- `list_tasks`, `cancel` — read/act on the single live task state.

**Fast window.** `delegate` starts an ACP prompt and races the final result against
a fixed **8 s** timer. ≤8 s → return the reply synchronously as the tool result
(clean function-calling; ~80 % of quick asks — the PoC's no-tool answer took ~2.3 s).
>8 s → return `task_id` + "продолжаю", drop to background.

**Concurrency.** One ACP process, **one `session/new` per delegation**, up to
`max_concurrent ≈ 3`. Verified: two sessions run concurrently, so a 3-minute task
never blocks a new question's 8 s window.

**Progress (interactivity).** On tool-boundary events, narrate **key milestones**
only ("смотрю YouTrack…", "теперь заметки…"), and only when the channel is free (do
not interrupt the user). Phrases come from a **template map** keyed on the event
(the tool name lives in the ACP `title`, e.g. `"terminal: uname -a"`, `kind=execute`),
spoken via `session.say` **without an LLM call** and **not written to `chat_ctx`**.
The manager keeps minimal live state (last tool + step counter) for
`list_tasks` / "как там?".

**Result reintegration.** On completion, append to `chat_ctx` a **synthetic tool
turn** — `assistant{tool_calls:[{id:new, name:"task_result", args:{task}}]}` +
`tool{tool_call_id:new, content:<answer>}` — then `session.generate_reply`. The
model sees an authoritative tool result and reads it instead of re-querying. The
context write happens **immediately and always** on completion (this also keeps
`list_tasks` in sync).

**Delivery of the spoken report.** Window + fallback: wait for a natural pause up to
**~15 s**, then a soft barge-in ("кстати, по той задаче…"). Guarantees the agent
comes back with a report (kills the unbounded idle gate and the lost-result drop).

**Failure / timeout (300 s cap).** Same synthetic tool turn with `content = error +
last step`, task state `failed`, honest report. No auto-retry.

## Considered Options

- **ACP streaming + hybrid fast-window** ✅ — one channel for progress + result +
  session id; both tool edges; concurrent sessions; clean tool-linked
  reintegration. Cost: a new ACP client + a long-lived process to supervise.
- **Keep CLI `hermes chat` + tail `agent.log` by session id** ❌ — minimal delta
  from today, but only the *completed* edge, prose log-scraping, fragile session-id
  correlation, and still no clean tool-result reintegration.
- **Fully synchronous, block the turn with verbal progress** ❌ — clean
  tool-result semantics but a long blocking turn inside a LiveKit tool is awkward,
  and "speak during a tool call" needs a side channel anyway.
- **Deferred/held-open tool result** ❌ — the "most correct" model (answer the
  original `tool_call_id` late) but LiveKit function tools resolve in-turn; holding
  one open blocks the turn and defeats backgrounding.
- **`hermes mcp serve`** ❌ — already rejected in 0007: it is a messaging bridge
  (Telegram/Discord), not a delegate-to-Hermes RPC.

## Consequences

- **Doctrine change.** "No MCP, direct CLI subprocess only" (`worker_tools.py:24`
  and the CLAUDE.md non-obvious note) is retired in favor of a persistent ACP
  client. Both must be updated.
- New failure surface: a long-lived process to health-check and restart (crash →
  respawn + fail in-flight tasks honestly). Worker startup gains an ACP spawn +
  `initialize`; Hermes-down stays recoverable (never a startup gate), per 0007.
- `run_command` narrows to literal shell only; the generic "run any whitelisted
  binary" framing of 0007 is gone.
- `--resume` session threading and the shared-`session_id` race disappear — ACP
  session ids are first-class and per-task.
- Milestone narration adds a small, bounded amount of agent speech; it is ephemeral
  (never in `chat_ctx`) so it does not bloat context or cost LLM calls.
- Assumption to hold at build time: ACP concurrent-session interleaving (validated
  in PoC) and the `title`/`kind` event shape (validated) must be re-checked if
  Hermes upgrades past **0.18.2**.

## PoC evidence (2026-07-16, live Pi, Hermes 0.18.2, ACP protocol v1)

A raw-ndjson ACP client (`scratchpad/acp_poc.py`) against the live `hermes acp`:

- **Concurrent sessions confirmed.** Session A ("17×3", no tools) returned at
  **20.90 s**; session B (three terminal commands) at **31.66 s**; B streamed **506**
  `session/update` events *after* A had already returned. A long task does not block
  a quick one.
- **Both tool edges stream.** Start: `sessionUpdate=tool_call title='terminal: uname
  -a' status=pending kind=execute id=tc-…`; finish: `tool_call_update
  status=completed id=tc-…`. Better than agent.log (completed-only). Tool name is in
  `title`; humanize via the template map.
- **No permission round-trips** with `--accept-hooks` — terminal tools auto-approve
  (matches the old `--yolo`); `session/request_permission` still handled defensively.
- Quick no-tool answers ~2.3 s (fit the 8 s window); a 3-terminal task ~13 s
  (correctly backgrounds); cold `initialize` ~8.5 s → spawn ACP at worker start.
