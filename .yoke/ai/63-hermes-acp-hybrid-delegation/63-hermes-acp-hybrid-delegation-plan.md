# Hermes delegation v2 — ACP streaming + hybrid fast-window delegation — implementation plan

**Task:** https://github.com/prineycom/voice-agent/issues/63
**Complexity:** complex
**Mode:** sub-agents
**Parallel:** true

Source of truth: `docs/adr/0022-hermes-acp-hybrid-delegation.md` (ADR-0022). No PoC
code survives in the tree — the ACP client is written from the ADR's protocol
description. Installed `livekit-agents==1.6.2` verified to expose `ChatContext.insert`,
`FunctionCall`/`FunctionCallOutput`, `session.say`, `generate_reply`, `wait_for_idle`,
`user_state`/`agent_state`.

## Design decisions

### DD-1: Config location for the new knobs

**Decision:** `config.yaml` under `worker_tools:` — `fast_window_s: 8`,
`max_concurrent: 3`, `task_timeout_s: 300`, `delivery_fallback_s: 15`; keep
`output_limit_chars`, `max_queued`.
**Rationale:** the existing Hermes tuning knobs already live there
(`infra/pi/agent/config.yaml:16-19`) and are read by `make_hermes_manager`
(`worker_tools.py:206-211`); `.env`/`AgentConfig` is reserved for transport/LiveKit/
wake-word (`config.py:60-102`).
**Alternative:** AgentConfig `.env` — rejected; wrong layer, breaks the existing
worker-tools config precedent.

### DD-2: Module split

**Decision:** new `infra/pi/agent/acp_client.py` owns transport + subprocess
supervision; `hermes_tasks.py` owns orchestration/state/narration/delivery.
**Rationale:** mirrors the `tts_plugin.py`/`stt_plugin.py` transport-vs-logic
separation; clean test seam (transport unit-tested with in-memory streams, manager
with a fake client).
**Alternative:** one module — rejected; untestable transport, L-sized file.

### DD-3: ops.js event shape — keep compatible, no lockstep edit

**Decision:** keep `{type:"tasks",running:[{label,elapsed}],queued:[]}` and event
kinds `delegated|done|error|cancelled`; live `last_tool`/`step` ride as optional
extra fields the client ignores.
**Rationale:** `ops.js:42-45` reads only `label`/`elapsed`; `ops.js:61-76` ignores
unknown kinds/fields. No frontend change needed.
**Alternative:** add a `milestone` event kind — rejected; narration is ephemeral per
ADR §Progress and must not become a durable feed entry.

### DD-4: Tool rename, not alias

**Decision:** `delegate_to_hermes→delegate`, `list_hermes_tasks→list_tasks`,
`cancel_hermes_tasks→cancel`; old names removed.
**Rationale:** ADR §Tools; `agent.py` import list (72-78) and single registration
point (517) update in lockstep — no other consumers exist.
**Alternative:** keep aliases — rejected; two names for one tool confuses the LLM
(the exact failure this epic fixes).

### DD-5: Hermes-down is recoverable, never a startup gate

**Decision:** failed/timed-out ACP `initialize` at startup logs and continues;
`delegate` returns an honest "Hermes недоступен" string; supervisor keeps respawning.
**Rationale:** run_command graceful-degradation doctrine (`worker_tools.py:22-23`,
ADR §Consequences); distinct from STT/TTS health gates which do abort
(`agent.py:243-252`).
**Alternative:** abort startup — rejected; voice agent must keep talking without Hermes.

### DD-6: Synthetic tool turn via ChatContext.insert

**Decision:** reintegration writes `FunctionCall(call_id=new, name="task_result",
arguments=json)` + `FunctionCallOutput(call_id=same, name="task_result",
output=answer, is_error=failed)` via `agent.chat_ctx.copy()` → `ctx.insert([...])` →
`agent.update_chat_ctx(ctx)`.
**Rationale:** `ChatContext.insert` accepts any ChatItem (`chat_context.py:453-457`);
`add_message` only accepts ChatMessage — which is why the old code used the
`role="system"` anti-pattern (`hermes_tasks.py:452`).
**Alternative:** keep system-message injection — rejected; the root cause of
re-delegation (issue §Root cause).

### DD-7: Fast-window race

**Decision:** `delegate` starts `_run_task` and awaits a per-task `first_result`
Future with `wait_for(fut, fast_window_s)`; resolved ≤8s → return answer as the tool
result and mark `delivered_synchronously` (completion path skips
reintegration+delivery); timeout → return `task_id`+"продолжаю".
**Rationale:** ADR §Fast window; the flag prevents double-answering.
**Alternative:** always background — rejected; quick asks must voice in the same turn.

### DD-8: Narration template map

**Decision:** module-level dict keyed on `kind` with tool-name refinement parsed from
`title` (`title.split(":",1)[0]`), → Russian phrase; fired only on new `tool_call`
start edges, only when the channel is free, via `session.say(phrase,
add_to_chat_ctx=False, allow_interruptions=True)`; unknown tools → generic phrase.
**Rationale:** ADR §Progress — no LLM call, never persisted, never interrupts.
**Alternative:** LLM-generated narration — rejected by ADR (latency + cost + risk of
double-speak).

### DD-9: Bounded delivery

**Decision:** race `wait_for_idle` vs `sleep(delivery_fallback_s)` with
FIRST_COMPLETED; then `generate_reply`, prefixing the soft barge-in phrase only when
the fallback timer won. Reintegration (DD-6) happens before the wait, immediate and
always.
**Rationale:** ADR §Delivery; kills the unbounded `_wait_until_idle` that lost results.
**Alternative:** unbounded idle wait — rejected; the exact bug being fixed.

### DD-10: Concurrency + admission

**Decision:** one ACP process; one `session/new` per `delegate`; gate at
`max_concurrent`; retain the existing FIFO queue/`max_queued` for overflow — a queued
task returns the "queued" directive synchronously and enters its 8s window only when
admitted.
**Rationale:** ADR §Concurrency; the queue is already UI-modeled (`ops.js`).
**Alternative:** reject on overflow — rejected; regression vs current behavior.

### DD-11: session/new params

**Decision:** `cwd` = worker process cwd (default `Path.cwd()`); `mcpServers=[]`.
**Rationale:** hooks auto-accepted via `--accept-hooks`; PoC saw no permission
round-trips (ADR §PoC). Re-verify on Hermes upgrade past 0.18.2.
**Alternative:** inherit MCP config — not needed per PoC; isolated to Task 4 if wrong.

## Non-blocking assumptions (stated, do not alter DAG)

1. The final answer arrives as the `session/prompt` RPC response; `session/update`
   carries progress. Re-verify against live `hermes acp` 0.18.2 (Task 4 internal).
2. `mcpServers=[]` suffices (DD-11; Task 4 internal).
3. Exact `kind` enum / `title` formats beyond `execute`/`terminal:` — unknown tools
   fall back to a generic narration phrase (Task 8 internal).

## Constraints

- Preserve EXACTLY the uncommitted wake-word edits in `infra/pi/agent/agent.py`
  (data_received handler ~369-392, `set_user_speaking_source` ~488-489) — all
  agent.py edits are additive around them.
- Tests: `cd infra/pi/agent && .venv/bin/python -m pytest tests/ -q`.
- All .md files in English. Commit convention:
  `#63 type(63-hermes-acp-hybrid-delegation): description`.

## Tasks

### Task 1: ACP JSON-RPC ndjson transport codec

- **Files:** `infra/pi/agent/acp_client.py` (create), `infra/pi/agent/tests/test_acp_client.py` (create)
- **Depends on:** none
- **Scope:** M
- **What:** `AcpConnection(reader, writer)` — request/response + notification codec
  over ndjson JSON-RPC, decoupled from any subprocess (takes asyncio stream
  reader/writer).
- **How:** Monotonic `id` counter; `async def request(method, params) -> result`
  registers a Future in `_pending[id]`, writes
  `json.dumps({"jsonrpc":"2.0","id":...,"method":...,"params":...})+"\n"`. A
  `_reader_loop` task reads lines and dispatches: `id`+`result/error` → resolve
  future; `method` without `id` → `on_notification(method, params)`; `method` with
  `id` → `on_server_request(method, params, id)` (for `session/request_permission`).
  Provide `respond(id, result)`. `aclose()` cancels the reader and fails pending
  futures.
- **Context:** `infra/pi/agent/tts_plugin.py:196-219` (lock+lifecycle shape);
  `infra/pi/agent/hermes_tasks.py:260-273` (best-effort publish/log idiom).
- **Verify:** `cd infra/pi/agent && .venv/bin/python -m pytest tests/test_acp_client.py -q`
  — request resolves, notification dispatches, via in-memory pipe.

### Task 2: Fake `hermes acp` ndjson subprocess harness

- **Files:** `infra/pi/agent/tests/conftest.py` (edit)
- **Depends on:** Task 1
- **Scope:** M
- **What:** Scripted ACP peer fixture ("real transport, scripted peer, recorded
  messages") answering `initialize`/`session/new` and emitting scripted
  `session/update` notifications then a final `session/prompt` result.
- **How:** `FakeAcpProc` mirroring `FakeProc`
  (`tests/test_hermes_tasks.py:83-108`) with real stdin/stdout asyncio streams
  (`os.pipe`/StreamReader) so `AcpConnection` runs unmodified; helper to script
  per-method responses and per-prompt update lists; record every client request.
  Expose a `fake_acp_exec` factory like `fake_exec_factory` (110-121) for
  monkeypatching `create_subprocess_exec`. Include a trivial self-test asserting the
  peer answers `initialize`.
- **Context:** `infra/pi/agent/tests/conftest.py:35-120` (FakeTTSServer scripted-peer
  idiom); `infra/pi/agent/tests/test_hermes_tasks.py:83-121` (FakeProc + factory).
- **Verify:** `cd infra/pi/agent && .venv/bin/python -m pytest tests/test_acp_client.py -q`.

### Task 3: ACP process supervisor + initialize + health/restart

- **Files:** `infra/pi/agent/acp_client.py` (edit), `infra/pi/agent/tests/test_acp_client.py` (edit)
- **Depends on:** Task 1, Task 2
- **Scope:** M
- **What:** `AcpClient` spawns `hermes acp --accept-hooks`, wires stdout/stdin into
  `AcpConnection`, runs `initialize {protocolVersion:1}`, health-checks and respawns
  on crash (failing in-flight sessions honestly).
- **How:** `create_subprocess_exec("hermes","acp","--accept-hooks", stdin=PIPE,
  stdout=PIPE, stderr=PIPE)`; `async def start()` = spawn+initialize with a bounded
  timeout, non-fatal on failure (DD-5). Lock-guarded `_ensure`/`_drop`/`aclose` per
  `tts_plugin.py:196-248`. A monitor detects process exit → mark dead, reject
  in-flight session futures, respawn lazily on next use.
- **Context:** `infra/pi/agent/tts_plugin.py:196-248`;
  `infra/pi/agent/worker_tools.py:142-152` (FileNotFoundError graceful string);
  `docs/adr/0022-hermes-acp-hybrid-delegation.md` (Transport + supervision sections).
- **Verify:** `cd infra/pi/agent && .venv/bin/python -m pytest tests/test_acp_client.py -q`
  — initialize succeeds against fake peer; killing the fake proc surfaces restart +
  honest in-flight failure.

### Task 4: ACP session/new + prompt streaming + permission handling

- **Files:** `infra/pi/agent/acp_client.py` (edit), `infra/pi/agent/tests/test_acp_client.py` (edit)
- **Depends on:** Task 3
- **Scope:** M
- **What:** `new_session(cwd, mcp_servers) -> session_id` and `prompt(session_id,
  text)` as an async generator yielding parsed `session/update` events and returning
  the final result; auto-answer `session/request_permission`.
- **How:** `session/new {cwd, mcpServers}`; `session/prompt {sessionId,
  prompt:[{type:"text", text}]}`. Route `session/update` notifications per session id
  into an `asyncio.Queue` the generator drains; the `session/prompt` RPC response
  terminates the stream with the final result (assumption 1).
  `on_server_request("session/request_permission")` → allow, defensively. Parse
  `tool_call`/`tool_call_update` fields (`title`, `kind`, `status`, `id`) into a
  small `AcpToolEvent`.
- **Context:** `docs/adr/0022-hermes-acp-hybrid-delegation.md` (handshake, event
  shapes, PoC evidence sections).
- **Verify:** `cd infra/pi/agent && .venv/bin/python -m pytest tests/test_acp_client.py -q`
  — scripted updates arrive in order, final result returned, scripted permission
  request auto-answered.

### Task 5: Config knobs

- **Files:** `infra/pi/agent/config.yaml` (edit)
- **Depends on:** none
- **Scope:** S
- **What:** Add `worker_tools:` knobs `fast_window_s: 8`, `max_concurrent: 3`,
  `task_timeout_s: 300`, `delivery_fallback_s: 15`; keep `max_queued`,
  `output_limit_chars`; update the stale async-delegation comment (lines 13-19).
- **How:** Edit the YAML block; comment each knob per DD-1 vocabulary.
- **Context:** `infra/pi/agent/config.yaml:7-19`.
- **Verify:** `cd infra/pi/agent && .venv/bin/python -c "import yaml,pathlib; yaml.safe_load(pathlib.Path('config.yaml').read_text())"`.

### Task 6: Manager skeleton — live state, UI feed, list_tasks, cancel, shutdown

- **Files:** `infra/pi/agent/hermes_tasks.py` (rewrite), `infra/pi/agent/tests/test_hermes_tasks.py` (replace)
- **Depends on:** Task 4, Task 2
- **Scope:** L
- **What:** New `HermesTask` state object (id, request, state∈{running,done,failed},
  last_tool, step, started_at, result, delivered flag) and
  `HermesTaskManager(acp_client, *, fast_window_s, max_concurrent, task_timeout_s,
  delivery_fallback_s, max_queued, output_limit)` holding the single source of truth
  (`_tasks: dict`). Implement `list_tasks` (live state — never stale), `cancel`,
  `shutdown`, `attach_session`, `set_publisher`, UI feed. `delegate`/`_run_task`
  stubbed to admit+mark running.
- **How:** Port `_emit`/`_safe_publish`/`_emit_tasks`/`_UI_RESEND_DELAYS` verbatim
  (`hermes_tasks.py:246-299`); `running` snapshot keeps `{label, elapsed}` + optional
  `last_tool` (DD-3). Drop `build_hermes_argv`, `_pending`, `_labels`,
  `_wait_until_idle`, `_inject_results`, `_session_id`. Rewrite the module docstring
  (remove CLI/`-Q`/resume narrative). Replace the test scaffolding: swap
  `FakeProc`/`fake_exec_factory` for the fake ACP client; extend `FakeChatCtx` with
  `insert()` and item classes; keep `FakeSession`/`FakeSessionWithAgent`.
- **Context:** `infra/pi/agent/hermes_tasks.py:79-304` (structure to replace),
  `246-299` (keep), `425-433` (idle probe to keep);
  `infra/pi/web/static/js/ops.js:28-49`;
  `infra/pi/agent/tests/test_hermes_tasks.py:19-121`.
- **Verify:** `cd infra/pi/agent && .venv/bin/python -m pytest tests/test_hermes_tasks.py -q`
  — list_tasks reflects live state incl. done; cancel; shutdown; UI snapshot shape.

### Task 7: `delegate` hybrid fast-window race

- **Files:** `infra/pi/agent/hermes_tasks.py` (edit), `infra/pi/agent/tests/test_hermes_tasks.py` (edit)
- **Depends on:** Task 6
- **Scope:** M
- **What:** Implement `delegate(request)`: admit (max_concurrent/queue), start
  `_run_task`, race the final result against `fast_window_s`; ≤8s → return answer
  synchronously; >8s → return `task_id`+directive, continue in background.
- **How:** `_run_task` = `new_session` → `async for ev in prompt(...)` (consumption
  body minimal for now) → resolve per-task `first_result` Future + set state `done`.
  `delegate` does `await asyncio.wait_for(fut, fast_window_s)`; on success set
  `delivered_synchronously=True`, return trimmed answer; on TimeoutError return
  `task_id` (DD-7). Timer pattern per `wake_state.py:201-217`.
- **Context:** `docs/adr/0022-hermes-acp-hybrid-delegation.md` (fast-window section);
  `infra/pi/agent/hermes_tasks.py:126-139` (old admit), `219-237` (slot/queue).
- **Verify:** `cd infra/pi/agent && .venv/bin/python -m pytest tests/test_hermes_tasks.py -q`
  — sub-window result returns synchronously (no generate_reply); over-window returns
  task_id and completes in background.

### Task 8: Milestone narration

- **Files:** `infra/pi/agent/hermes_tasks.py` (edit), `infra/pi/agent/tests/test_hermes_tasks.py` (edit)
- **Depends on:** Task 7
- **Scope:** M
- **What:** In `_run_task`'s update loop, map tool-start events to Russian template
  phrases spoken via `session.say`, channel-free only, never chat_ctx, no LLM; update
  live `last_tool`/`step`.
- **How:** Module-level `NARRATION` dict keyed on `kind`+parsed tool name (DD-8); on
  a `tool_call` start edge, update state, dedupe, and if idle call
  `session.say(phrase, add_to_chat_ctx=False, allow_interruptions=True)`. Unknown
  tools → generic phrase.
- **Context:** `docs/adr/0022-hermes-acp-hybrid-delegation.md` (Progress section);
  `infra/pi/agent/agent.py:144-183` (stream side-effect shape);
  `infra/pi/agent/hermes_tasks.py:425-433` (idle probe).
- **Verify:** `cd infra/pi/agent && .venv/bin/python -m pytest tests/test_hermes_tasks.py -q`
  — scripted tool_call events produce `session.say` calls; none when channel busy;
  `last_tool` updates.

### Task 9: Synthetic tool-turn reintegration

- **Files:** `infra/pi/agent/hermes_tasks.py` (edit), `infra/pi/agent/tests/test_hermes_tasks.py` (edit)
- **Depends on:** Task 8
- **Scope:** M
- **What:** On background completion, write the paired
  `FunctionCall`+`FunctionCallOutput` ("task_result") into chat_ctx immediately and
  always (skipped only when `delivered_synchronously`).
- **How:** `_reintegrate(task)`: `chat_ctx = agent.chat_ctx.copy()`;
  `chat_ctx.insert([FunctionCall(...), FunctionCallOutput(..., is_error=failed)])`;
  `await agent.update_chat_ctx(chat_ctx)` (DD-6). Replaces `_inject_results`
  (435-460). Import from `livekit.agents.llm`.
- **Context:** venv `livekit/agents/llm/chat_context.py:346-397` (FunctionCall/Output
  fields), `453-457` (insert); `infra/pi/agent/hermes_tasks.py:435-460` (anti-pattern
  to replace); update `FakeChatCtx.insert` in tests.
- **Verify:** `cd infra/pi/agent && .venv/bin/python -m pytest tests/test_hermes_tasks.py -q`
  — after a background result, chat_ctx holds function_call + function_call_output
  (not role=system).

### Task 10: Bounded delivery (window + soft barge-in)

- **Files:** `infra/pi/agent/hermes_tasks.py` (edit), `infra/pi/agent/tests/test_hermes_tasks.py` (edit)
- **Depends on:** Task 9
- **Scope:** M
- **What:** Deliver the spoken report by racing `wait_for_idle` against
  `delivery_fallback_s`, then `generate_reply` (soft barge-in prefix when the
  fallback fired). Reintegration precedes delivery.
- **How:** Replace `_delivery_worker`/`_wait_until_idle` (369-433) with a
  per-completion `_deliver(task)`: `await _reintegrate`; `asyncio.wait({idle_task,
  sleep(fallback)}, FIRST_COMPLETED)`; build instructions (barge-in phrase "кстати,
  по той задаче…" if the timer won) → `session.generate_reply(instructions=...,
  allow_interruptions=True)`; guard exceptions per `hermes_tasks.py:389-394` (DD-9).
- **Context:** `docs/adr/0022-hermes-acp-hybrid-delegation.md` (Delivery section);
  `infra/pi/agent/wake_state.py:201-217` (timer pattern).
- **Verify:** `cd infra/pi/agent && .venv/bin/python -m pytest tests/test_hermes_tasks.py -q`
  — busy session → delivered after idle within window; permanently-busy → delivered
  at fallback (bounded).

### Task 11: Failure / timeout honest reporting

- **Files:** `infra/pi/agent/hermes_tasks.py` (edit), `infra/pi/agent/tests/test_hermes_tasks.py` (edit)
- **Depends on:** Task 10
- **Scope:** M
- **What:** On ACP error, `task_timeout_s` (300 s) expiry, or process crash, report
  via the same synthetic tool-turn shape with content=error+last step, state
  `failed`, no auto-retry; agent keeps talking.
- **How:** Wrap `_run_task` prompt consumption in `wait_for(..., task_timeout_s)`;
  on TimeoutError/exception set `state=failed`, `result=error+last_tool`, and (if not
  synchronous) run `_reintegrate`(is_error=True)+`_deliver`. `delegate`'s fast window
  returns the honest error if it fails within 8 s. Model on
  `hermes_tasks.py:322-351`.
- **Context:** `docs/adr/0022-hermes-acp-hybrid-delegation.md` (Failure section);
  `infra/pi/agent/hermes_tasks.py:322-351`; DD-5.
- **Verify:** `cd infra/pi/agent && .venv/bin/python -m pytest tests/test_hermes_tasks.py -q`
  — scripted ACP error and timeout both yield `failed` task, `is_error`
  function_call_output, spoken report, no crash/retry.

### Task 12: Narrow `run_command` + module docstring doctrine

- **Files:** `infra/pi/agent/worker_tools.py` (edit), `infra/pi/agent/tests/test_worker_tools.py` (edit)
- **Depends on:** none
- **Scope:** S
- **What:** Restrict `run_command` to literal shell commands (docstring forbids
  free-form intent) and rewrite the module docstring — "No MCP, direct CLI subprocess
  only" (line 24) is now wrong.
- **How:** Rewrite the `run_command` docstring (109-122): literal-shell-only,
  free-form → use `delegate`, drop Hermes `--resume` guidance. Rewrite module
  docstring (1-32) for the ACP client + narrowed run_command. Exec/whitelist
  mechanics unchanged. Adjust `test_worker_tools.py` assertions referencing
  Hermes-via-run_command wording.
- **Context:** `infra/pi/agent/worker_tools.py:1-32, 109-122`;
  `docs/adr/0022-hermes-acp-hybrid-delegation.md` (Tools section).
- **Verify:** `cd infra/pi/agent && .venv/bin/python -m pytest tests/test_worker_tools.py -q`.

### Task 13: New tool adapters + `make_hermes_manager` ACP wiring

- **Files:** `infra/pi/agent/worker_tools.py` (edit), `infra/pi/agent/tests/test_hermes_tasks.py` (edit — adapter test)
- **Depends on:** Task 11, Task 5, Task 12
- **Scope:** M
- **What:** Replace `delegate_to_hermes`/`list_hermes_tasks`/`cancel_hermes_tasks`
  with `delegate`/`list_tasks`/`cancel`; update `make_hermes_manager` to accept the
  `AcpClient` and read the new knobs.
- **How:** Rename the three `@function_tool`s (docstrings per ADR §Tools; `delegate`
  = free-form intent, returns sync-or-ack). `make_hermes_manager(acp_client)` reads
  `fast_window_s`/`max_concurrent`/`task_timeout_s`/`delivery_fallback_s`/
  `max_queued`/`output_limit_chars` via the existing `_num` helper (200-211). Keep
  `_manager` (214-218).
- **Context:** `infra/pi/agent/worker_tools.py:188-246`;
  `infra/pi/agent/tests/test_hermes_tasks.py:391-409` (adapter test to update).
- **Verify:** `cd infra/pi/agent && .venv/bin/python -m pytest tests/ -q -k "worker or tasks"`.

### Task 14: agent.py startup spawn + wiring + registration

- **Files:** `infra/pi/agent/agent.py` (edit — additive), `infra/pi/agent/tests/test_agent.py` (create)
- **Depends on:** Task 13, Task 3
- **Scope:** M
- **What:** Construct+start the `AcpClient` at startup (before `session.start`,
  recoverable per DD-5), pass to `make_hermes_manager`, register
  `add_shutdown_callback(acp_client.aclose)`, update imports (72-78) and the tool
  list (517). Preserve wake-word edits exactly.
- **How:** Import `AcpClient` + renamed tools; at ~264 build `acp_client`,
  `await acp_client.start()` (try/except → log, continue),
  `hermes_manager = make_hermes_manager(acp_client)`; keep `set_publisher` (277-281)
  and `userdata` (453). Tool list (517) → `[delegate, cancel, list_tasks,
  run_command]`. DO NOT touch the `data_received` handler (~369-392) or
  `set_user_speaking_source` (~488-489). Add `test_agent.py`: tool registration set;
  failed ACP start does not abort the entrypoint (monkeypatch spawn).
- **Context:** `infra/pi/agent/agent.py:72-78, 261-281, 431-454, 510-521`;
  uncommitted wake blocks ~369-392 and ~488-489 (preserve exactly).
- **Verify:** `cd infra/pi/agent && .venv/bin/python -m pytest tests/test_agent.py -q`;
  wake blocks unchanged in `git diff infra/pi/agent/agent.py`.

### Task 15: Rewrite `skills/hermes.md`

- **Files:** `infra/pi/agent/skills/hermes.md` (edit)
- **Depends on:** none
- **Scope:** S
- **What:** New LLM contract: `delegate` for any free-form intent; `run_command`
  literal shell only (free-form explicitly forbidden — fixes the 01-07 failure);
  `list_tasks`/`cancel`; drop the "always background" framing (fast window may return
  synchronously).
- **How:** Rewrite per ADR §Tools; forbid "найди…/сделай…" in `run_command`.
- **Context:** `infra/pi/agent/skills/hermes.md`;
  `docs/adr/0022-hermes-acp-hybrid-delegation.md` (Tools section).
- **Verify:** manual read; `cd infra/pi/agent && .venv/bin/python -m pytest tests/test_soul.py -q` (skill loading unaffected).

### Task 16: Update `docs/architecture/overview.md`

- **Files:** `docs/architecture/overview.md` (edit)
- **Depends on:** none
- **Scope:** M
- **What:** Refresh the stale Hermes sections (~54-56, 118, 159-192, 312-326,
  388-389, 432): diagram, module descriptions, tool list, `hermes chat` flow → ACP.
- **How:** Replace `run_command → hermes chat CLI`, `delegate → hermes chat -q…`, and
  the tool-wrapper list with the ACP client + `delegate`/fast-window/synthetic-
  tool-turn model. Point `docs/superpowers/specs/2026-06-25-hermes-async-delegation-design.md`
  readers at ADR-0022.
- **Context:** `docs/architecture/overview.md` (listed lines);
  `docs/adr/0022-hermes-acp-hybrid-delegation.md` (whole).
- **Verify:** `grep -n "hermes chat\|delegate_to_hermes" docs/architecture/overview.md`
  returns nothing.

### Task 17: CLAUDE.md note + commit ADR-0007 & ADR-0022

- **Files:** `CLAUDE.md` (edit), `docs/adr/0007-hermes-cli-delegation.md` (commit
  existing working-tree edit), `docs/adr/0022-hermes-acp-hybrid-delegation.md`
  (commit untracked file)
- **Depends on:** none
- **Scope:** S
- **What:** Add a non-obvious note that Hermes is reached via a long-lived
  `hermes acp` ACP client (retiring "direct CLI subprocess only"); commit ADR-0007
  (superseded pointer, already edited in tree) and ADR-0022.
- **How:** Append a bullet under CLAUDE.md "Non-obvious"; `git add` the two ADR
  files; commit `#63 docs(63-hermes-acp-hybrid-delegation): ...`.
- **Context:** `CLAUDE.md` (Non-obvious section); `git status` (0007 modified,
  0022 untracked).
- **Verify:** `git status --short docs/adr` clean after commit; `grep -n acp CLAUDE.md`.

### Task 18: Validation

- **Files:** —
- **Depends on:** all
- **Scope:** S
- **What:** Run full validation; verify acceptance-criteria coverage.
- **Context:** —
- **Verify:** `cd /home/priney/repos/voice-agent/infra/pi/agent && .venv/bin/python -m pytest tests/ -q` — all green.

## File-intersection matrix (hot files)

| File | Tasks |
|---|---|
| `acp_client.py` + `tests/test_acp_client.py` | 1, 3, 4 (serial) |
| `tests/conftest.py` | 2 |
| `hermes_tasks.py` + `tests/test_hermes_tasks.py` | 6, 7, 8, 9, 10, 11, 13 (serial) |
| `worker_tools.py` | 12, 13 (serial) |
| `agent.py` + `tests/test_agent.py` | 14 |
| `config.yaml` | 5 |
| `skills/hermes.md` | 15 |
| `docs/architecture/overview.md` | 16 |
| `CLAUDE.md` + ADRs | 17 |

No two parallel tasks share a file.

## Execution

- **Mode:** sub-agents
- **Parallel:** true
- **Reasoning:** 18 tasks; six independent tasks form Wave 1; the ACP chain
  (1→2→3→4) and the manager chain (6→…→11) are serial on shared files; docs/config
  tasks are file-disjoint.
- **Order:**
  Group 1 (parallel): Task 1, Task 5, Task 12, Task 15, Task 16, Task 17
  ─── barrier ───
  Group 2 (sequential): Task 2 → Task 3 → Task 4
  ─── barrier ───
  Group 3 (sequential): Task 6 → Task 7 → Task 8 → Task 9 → Task 10 → Task 11
  ─── barrier ───
  Group 4 (sequential): Task 13 → Task 14
  ─── barrier ───
  Group 5: Task 18 (Validation)

Critical path: T1→T2→T3→T4→T6→T7→T8→T9→T10→T11→T13→T14→T18.

## Verification

Acceptance criteria from issue #63:

- [ ] A quick ask (<8 s) returns synchronously and is voiced in the same turn.
- [ ] A slow ask (>8 s) acks immediately, keeps the agent responsive, and later comes
      back with a report even in a chatty conversation (bounded delivery, no lost
      result).
- [ ] While a task runs, the agent narrates key milestones and answers "как там?"
      from live state (never a stale "still running" after completion).
- [ ] After a background result, asking the agent to repeat/act on it is answered
      from context — no re-delegation to Hermes.
- [ ] A quick question during a long background task is not blocked (concurrent
      sessions).
- [ ] `run_command` is used only for literal shell; free-form asks route to
      `delegate`.
- [ ] Failures/timeouts are reported honestly, agent keeps talking (Hermes-down not
      a crash).
- [ ] Agent test suite green.

## Materials

- `docs/adr/0022-hermes-acp-hybrid-delegation.md` — source of truth (supersedes
  ADR-0007).
- GitHub issue #63 — epic body with root-cause analysis and PoC evidence
  (Hermes 0.18.2, ACP protocol v1).
- `docs/adr/0007-hermes-cli-delegation.md` — superseded design (pointer edit already
  in working tree).
- PoC numbers: cold initialize ~8.5 s; quick no-tool ~2.3 s; concurrent sessions
  verified (A@20.9 s, B@31.66 s, 506 events after A returned). PoC script itself is
  NOT in the tree — client written from the ADR.
