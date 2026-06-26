---
tags:
  - voice-agent
  - agent-worker
  - hermes
---

# Hermes Async Delegation — Design

Make Hermes delegation non-blocking so the Agent Worker stays conversational
while Hermes works in the background, acknowledges immediately, reports
proactively when done, and supports a queue, a concurrency limit, progress
nudges, and cancellation.

## Problem

`run_command` is a synchronous `function_tool`: the `AgentSession` awaits the
Hermes subprocess (up to 120s), so the agent is stuck "thinking" and deaf to the
user for the whole call. Desired UX: the agent says a short ack ("хорошо, сейчас
гляну"), keeps talking with the user, and announces the Hermes result when it
arrives — purely in the background.

## Core flow — `delegate_to_hermes(request)`

1. **Spawn in background.** The tool enqueues/starts a Hermes subprocess via
   `asyncio.create_task` (NOT awaited) and returns immediately with a directive
   string: *"Запущено в фоне. Дай пользователю одну короткую фразу-подтверждение
   и продолжай. Результат придёт позже."*
2. **Ack.** The LLM voices the acknowledgement; the turn ends; the agent is
   listening again and answers the user normally.
3. **Completion.** When Hermes finishes, the background runner calls
   `session.generate_reply(instructions="Hermes вернул: …; сообщи пользователю в
   своём стиле")` so the agent re-voices it in SOUL style (hands-and-mouth split
   preserved).
4. **Error/timeout.** The agent proactively says it could not complete the task.

`session_id` / `--resume` continuity is owned by the worker (in-memory per job),
not threaded through the LLM. The skill is simplified accordingly.

## HermesTaskManager (one per job)

Stored in `session.userdata`; tools reach it via `context.session.userdata`. The
manager holds the session reference (captured on first tool call) for proactive
delivery and a registry of tasks: `id`, short label (derived from the request),
`asyncio.Task`, subprocess handle, state (`queued|running|done|cancelled|error`),
`started_at`.

### Concurrency + queue
- `MAX_CONCURRENT` running tasks (default **3**), gated by an `asyncio.Semaphore`.
- Overflow goes to a FIFO queue capped at `MAX_QUEUED` (default **5**); a freed
  slot pulls the next queued task.
- Queue full → the tool returns a directive telling the agent to ask the user to
  wait (spoken, not a silent drop).

### Progress nudges
- A task running longer than `PROGRESS_INTERVAL` (default **25s**) triggers a
  proactive nudge via `generate_reply("скажи одной фразой, что ещё ищешь")`.
- Capped at `PROGRESS_MAX_UPDATES` (default **3**). Past `TASK_TIMEOUT`
  (default **300s**) the task is cancelled and reported as failed.

### Cancellation
- Voice: `cancel_hermes_tasks(hint="")` — cancels active tasks (`Task.cancel()` +
  `proc.kill()`). No hint → all; with hint → best-effort substring match on the
  request. The agent confirms the cancellation.
- Auto on shutdown: when the session ends (user disconnects), the manager cancels
  all tasks and kills their subprocesses — no orphaned Hermes processes. Wired via
  a job shutdown callback.

### Visibility
- `list_hermes_tasks()` — short summary ("в работе: поиск погоды; в очереди:
  проверка почты") so the agent can answer "чем занят?".

### Ordered delivery
All proactive speech (results, nudges, errors) is serialized through an
`asyncio.Lock` and waits for the session to be idle, so the agent never speaks
two things at once and does not cut off the user or itself.

## Tools exposed to the LLM
`delegate_to_hermes`, `cancel_hermes_tasks`, `list_hermes_tasks`, plus the
existing generic `run_command` for rare synchronous whitelisted commands. The
skill steers Hermes work to `delegate_to_hermes`.

## Files
- `infra/pi/agent/worker_tools.py` — task manager + 3 new tools + Hermes
  session-id store. Keep `run_command`.
- `infra/pi/agent/agent.py` — `AgentSession(userdata=HermesTaskManager(...))`,
  register the tools, add a shutdown callback that cancels all tasks.
- `infra/pi/agent/config.yaml` — `worker_tools.hermes_max_concurrent`,
  `hermes_max_queued`, `hermes_task_timeout_seconds`,
  `hermes_progress_interval_seconds`, `hermes_progress_max_updates`.
- `infra/pi/agent/skills/hermes.md` — rewrite for the async flow.
- `docs/adr/0007-hermes-cli-delegation.md` — note the async manager evolution.

## Config defaults
```yaml
worker_tools:
  allowed_commands: [hermes]
  hermes_max_concurrent: 3
  hermes_max_queued: 5
  hermes_task_timeout_seconds: 300
  hermes_progress_interval_seconds: 25
  hermes_progress_max_updates: 3
```

## Tests (TDD)
- `delegate_to_hermes` returns immediately (does not await the subprocess) and
  spawns a background task.
- On completion the manager calls `session.generate_reply` with the result
  (mocked session).
- Limit: with `MAX_CONCURRENT=3`, a 4th task queues; queue overflow returns the
  refusal directive.
- Progress nudge fires after the interval and never exceeds the cap.
- Cancellation: `cancel_hermes_tasks` cancels the task and kills the subprocess;
  shutdown cancels all.
- Error/timeout produces a proactive failure message, never raises.

## Definition of done
"Посмотри X" → "ок, гляну" → keep chatting, agent answers → (if slow) "ещё ищу"
→ agent announces the result. "Отмени" cancels. Multiple tasks respect the
limit/queue.

## Out of scope
Persisting the queue across job restarts; cross-room task sharing; per-task
priorities; streaming partial Hermes output.
