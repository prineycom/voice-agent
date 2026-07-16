"""Worker tools — literal-shell escape hatch + async Hermes delegation.

Hermes delegation runs over a long-lived `hermes acp` (Agent Client Protocol)
streaming client — one persistent process spawned at worker startup, one ACP
session per delegated task (see docs/adr/0022-hermes-acp-hybrid-delegation.md,
which supersedes docs/adr/0007-hermes-cli-delegation.md). Free-form intent
("найди мои задачи", "сделай X") goes through the `delegate` tool, which races
the task against an 8s fast window and backgrounds it past that, later
reintegrating the result as a synthetic tool turn.

This module also exposes `run_command(args)`, a narrow, separate escape hatch:
a `livekit.agents.function_tool` that lets the agent's LLM run a LITERAL shell
command line whose first token is on the whitelist defined in
`infra/pi/agent/config.yaml` (`worker_tools.allowed_commands`, default
`["hermes"]`). It is not a natural-language interface — free-form asks are
explicitly out of scope for `run_command` and must go to `delegate` instead.

Design (see CONTEXT.md → Hermes Tools / run_command):
- The LLM composes the full CLI args string (taught by skills/hermes.md).
- `args` is parsed with `shlex.split`; the FIRST token is the command name and
  must be in the whitelist — anything else is rejected before exec. This is the
  only safety boundary: the whitelist is the set of trusted binaries, not a
  sandbox of their flags. Operators add a trusted command to `config.yaml` and
  a usage pattern to skills/hermes.md; no code change, restart to pick up.
- The command runs as an async subprocess (`asyncio.create_subprocess_exec`)
  so the event loop is never blocked. `run_command` is a coroutine; the
  livekit-agents turn awaits its return, then the LLM re-voices the result
  in-turn (hands-and-mouth split — the worker never speaks Hermes raw output
  verbatim; the LLM rewrites it in SOUL style).
- Graceful degradation: a non-whitelisted command, a missing binary, or a
  non-zero exit returns a clear error STRING (never raises) so the agent keeps
  talking instead of crashing the turn. Hermes being down is a recoverable
  condition, not a startup-fatal one (unlike STT/TTS, which gate startup). The
  long-lived ACP client backing `delegate` follows the same doctrine: a crash
  is a respawn-and-report-honestly event, never a startup gate.

Config (`infra/pi/agent/config.yaml`):
    worker_tools:
      allowed_commands:
        - hermes
        # - git      # add later if needed
        # - docker   # add later
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shlex
from pathlib import Path

import yaml
from livekit.agents import function_tool
from livekit.agents.voice.events import RunContext

from acp_client import AcpClient
from hermes_tasks import (
    DEFAULT_DELIVERY_FALLBACK_S,
    DEFAULT_FAST_WINDOW_S,
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_MAX_QUEUED,
    DEFAULT_OUTPUT_LIMIT,
    DEFAULT_TASK_TIMEOUT,
    HermesTaskManager,
)

log = logging.getLogger("agent")

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"
DEFAULT_ALLOWED_COMMANDS = ("hermes",)
# Cap how long a single run_command call may block the turn. Hermes itself has
# its own internal max_turns; this is a hard ceiling so a runaway task can't
# wedge the voice turn forever. Tunable in config (worker_tools.timeout_seconds).
DEFAULT_TIMEOUT_SECONDS = 120.0
# Note: this is a hard ceiling on a single run_command call, distinct from
# Hermes's own `--max-turns` (default 90 tool-call iterations). 120s gives a
# delegated task room to finish while preventing a runaway from wedging the
# voice turn forever. Tune via worker_tools.timeout_seconds in config.yaml.

_ALLOWED: tuple[str, ...] | None = None
_TIMEOUT: float | None = None


def _load_tool_config() -> tuple[tuple[str, ...], float]:
    """Read the allowed-commands whitelist + timeout from config.yaml.

    Falls back to defaults if the file or key is missing — the worker must still
    boot (tools just reject everything not whitelisted). Returns (commands, timeout).
    """
    try:
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        log.warning("worker tool config not found at %s; using default whitelist", CONFIG_PATH)
        return DEFAULT_ALLOWED_COMMANDS, DEFAULT_TIMEOUT_SECONDS
    wt = raw.get("worker_tools") or {}
    cmds = wt.get("allowed_commands") or list(DEFAULT_ALLOWED_COMMANDS)
    timeout = wt.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS
    return tuple(str(c) for c in cmds), float(timeout)


def _allowed_commands() -> tuple[str, ...]:
    global _ALLOWED
    if _ALLOWED is None:
        _ALLOWED, _TIMEOUT = _load_tool_config()  # type: ignore[misc]
    return _ALLOWED


def _timeout_seconds() -> float:
    global _ALLOWED, _TIMEOUT
    if _TIMEOUT is None:
        _ALLOWED, _TIMEOUT = _load_tool_config()  # type: ignore[misc]
    return _TIMEOUT


def _invalidate_config_cache() -> None:
    """Test hook: force the next call to re-read config.yaml."""
    global _ALLOWED, _TIMEOUT
    _ALLOWED = None
    _TIMEOUT = None


@function_tool
async def run_command(args: str) -> str:
    """Run a LITERAL shell command line on the Pi and return its output.

    This tool accepts ONLY a literal command line, e.g. `run_command("uname -a")`
    or `run_command("hermes memory list")`. The first token must be a
    whitelisted command (default: `hermes`); anything else is rejected. Quote
    arguments that contain spaces with single quotes.

    FORBIDDEN: free-form natural-language intent or requests, e.g.
    `run_command("найди мои задачи")` or `run_command("сделай X")`. Those must
    go to the `delegate` tool, not here — `run_command` does not interpret
    intent, it only execs the literal command line you give it.

    Returns the command's stdout/stderr tail. On rejection, missing binary,
    timeout, or non-zero exit, returns a short error string describing the
    failure — never raises.
    """
    try:
        tokens = shlex.split(args)
    except ValueError as exc:
        return f"run_command: malformed args ({exc}). Quote any string with spaces."

    if not tokens:
        return "run_command: empty command."

    command = tokens[0]
    allowed = _allowed_commands()
    if command not in allowed:
        log.warning("run_command rejected non-whitelisted command %r (allowed: %s)", command, allowed)
        return (
            f"run_command: '{command}' is not allowed. "
            f"Whitelisted commands: {', '.join(allowed)}."
        )

    timeout = _timeout_seconds()
    log.info("run_command exec: %s", args)
    try:
        proc = await asyncio.create_subprocess_exec(
            *tokens,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return f"run_command: '{command}' not found on PATH. Is it installed on this host?"
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("run_command spawn failed")
        return f"run_command: failed to start '{command}': {exc}"

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        # Best-effort kill so a runaway child doesn't linger.
        with contextlib.suppress(Exception):
            proc.kill()
        return f"run_command: '{command}' timed out after {timeout:.0f}s."

    stdout = stdout_b.decode("utf-8", errors="replace").strip()
    stderr = stderr_b.decode("utf-8", errors="replace").strip()

    if proc.returncode != 0:
        tail = stderr[-500:] if stderr else stdout[-500:]
        log.warning("run_command %r exited %s; stderr: %s", command, proc.returncode, tail)
        return f"run_command: '{command}' exited {proc.returncode}: {tail}"

    # Hermes emits the `session_id:` line on STDERR in -Q mode (not stdout).
    # The LLM needs it to resume the same Hermes session via --resume, so fold
    # any `session_id:` line from stderr into the returned output. Other stderr
    # noise (spinner remnants, warnings) is ignored.
    sid_lines = [ln for ln in stderr.splitlines() if ln.strip().startswith("session_id:")]
    if not stdout and not sid_lines:
        return "(no output)"
    parts = []
    if sid_lines:
        parts.append(sid_lines[-1].strip())
    if stdout:
        parts.append(stdout)
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Async Hermes delegation: background task manager + thin function_tool adapters
# --------------------------------------------------------------------------- #
def make_hermes_manager(acp_client: AcpClient | None = None) -> HermesTaskManager:
    """Construct a HermesTaskManager over an AcpClient using config.yaml knobs.

    Reads the ACP hybrid-delegation knobs from ``worker_tools.*`` (ADR-0022);
    missing keys fall back to the module defaults so the worker always boots.

    ``acp_client`` lets the caller pass an AcpClient it created (and will
    ``start()``) eagerly at worker startup — cold ``initialize`` is ~8.5s, so
    production spawns it up front rather than paying that on the first delegation.
    When omitted, a fresh AcpClient is constructed and spawned lazily on first use
    (respawn-on-demand), which keeps tests and standalone use zero-config.

    NOTE: eagerly calling ``start()`` on the client is the caller's job — this
    factory only wires the manager; it never blocks startup itself. The manager is
    stored in AgentSession.userdata and the tool adapters reach it from there.
    """
    try:
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raw = {}
    wt = raw.get("worker_tools") or {}

    def _num(key, default, cast):
        try:
            return cast(wt.get(key, default))
        except (TypeError, ValueError):
            return default

    return HermesTaskManager(
        acp_client if acp_client is not None else AcpClient(),
        fast_window_s=_num("fast_window_s", DEFAULT_FAST_WINDOW_S, float),
        max_concurrent=_num("max_concurrent", DEFAULT_MAX_CONCURRENT, int),
        task_timeout_s=_num("task_timeout_s", DEFAULT_TASK_TIMEOUT, float),
        delivery_fallback_s=_num("delivery_fallback_s", DEFAULT_DELIVERY_FALLBACK_S, float),
        max_queued=_num("max_queued", DEFAULT_MAX_QUEUED, int),
        output_limit=_num("output_limit_chars", DEFAULT_OUTPUT_LIMIT, int),
    )


def _manager(context: RunContext) -> HermesTaskManager:
    """Fetch the per-session task manager and bind the live session to it."""
    manager: HermesTaskManager = context.session.userdata
    manager.attach_session(context.session)
    return manager


@function_tool
async def delegate(context: RunContext, request: str) -> str:
    """Delegate ANY free-form ask to Hermes — your hands for real work.

    Use this for anything needing tools: web search, files, terminal, memory,
    YouTrack, messaging, multi-step work — pass a complete, specific
    natural-language `request` ("найди…", "проверь…", "сделай…").

    HYBRID timing — read the return value:
    - FAST task (~8s): returns the ANSWER directly. Voice it to the user in THIS
      same turn, like any tool result.
    - SLOW task: returns a `task_id` and a "continues in the background" ack. Give
      the user one short acknowledgement, keep talking, and do NOT wait or re-ask.
      The result arrives on its own later as a `task_result` tool turn — report it
      then. NEVER re-delegate the same request to check on it; use `list_tasks`
      for status instead.
    """
    manager = _manager(context)
    return await manager.delegate(request)


@function_tool
async def cancel(context: RunContext, hint: str = "") -> str:
    """Cancel delegated Hermes tasks. Call when the user says stop / отмени / забей.

    `hint` matches a task by its `task_id` or a case-insensitive substring of its
    label; an empty `hint` cancels ALL active tasks. Returns a directive to
    confirm the cancellation to the user.
    """
    manager = _manager(context)
    return await manager.cancel(hint)


@function_tool
async def list_tasks(context: RunContext) -> str:
    """Live status of delegated Hermes tasks — call for "как там?", "чем занят?".

    Reports what is running (current step/tool), what is queued, and recent
    results, read straight from live task state. Use this to answer a status
    question about an in-flight task — never re-delegate to check on one.
    """
    manager = _manager(context)
    return manager.list_tasks()
