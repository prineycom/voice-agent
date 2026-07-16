"""Wiring tests for the Agent Worker entrypoint (agent.py).

Two things are asserted here, both introduced when the long-lived ``hermes acp``
ACP client was wired into worker startup (ADR-0022, #63):

1. Tool registration — the entrypoint registers the RENAMED delegation tools
   (``delegate`` / ``cancel`` / ``list_tasks`` / ``run_command``) as livekit
   ``FunctionTool``s, and the old ``delegate_to_hermes`` name is gone.
2. Eager-spawn resilience — ``_start_hermes`` spawns + initializes the ACP client
   at startup and ALWAYS returns a working manager, even when Hermes cannot be
   brought up. It must never raise (Hermes is never a startup gate, unlike
   STT/TTS): a missing binary, or even an unexpected exception out of ``start()``,
   degrades delegation gracefully instead of aborting the job.

The helper is unit-tested directly (running the full ``entrypoint`` needs a live
JobContext), driving the REAL ``AcpClient`` against the scripted ``FakeAcpProc``
peer from conftest via a monkeypatched ``create_subprocess_exec`` — the same
harness ``test_acp_client.py`` uses.
"""

import asyncio

import pytest
from livekit.agents import FunctionTool

import agent
from hermes_tasks import HermesTaskManager
from acp_client import AcpClient
from conftest import FakeAcpProc

TOOL_NAMES = ("delegate", "cancel", "list_tasks", "run_command")


def _tool_name(tool) -> str:
    """The registered name of a livekit FunctionTool (via its ``.info``)."""
    return tool.info.name


# ---------------------------------------------------------------------------
# 1. Tool registration
# ---------------------------------------------------------------------------


def test_module_exposes_the_four_renamed_function_tools():
    # The entrypoint's tool list references these module names directly; each must
    # be a livekit FunctionTool carrying its renamed name (ADR-0022 rename).
    for name in TOOL_NAMES:
        tool = getattr(agent, name)
        assert isinstance(tool, FunctionTool), f"{name} is not a FunctionTool"
        assert _tool_name(tool) == name


def test_old_delegate_to_hermes_name_is_gone():
    # The pre-ADR-0022 tool name must not linger anywhere on the module.
    assert not hasattr(agent, "delegate_to_hermes")


def test_greeting_agent_registers_exactly_the_four_tools():
    # Construct the agent the way the entrypoint does (same tool list) and assert
    # the livekit Agent registered exactly those four tools, by name.
    greeter = agent.GreetingAgent(
        instructions="test",
        tools=[agent.delegate, agent.cancel, agent.list_tasks, agent.run_command],
        greeting="привет",
    )
    registered = {_tool_name(t) for t in greeter.tools}
    assert registered == set(TOOL_NAMES)


# ---------------------------------------------------------------------------
# 2. Eager-spawn resilience (_start_hermes)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_hermes_returns_manager_wired_to_a_started_client(
    fake_acp_exec, monkeypatch
):
    # Happy path: the ACP client spawns + initializes, and the returned manager is
    # wired to that live client (available), so the first delegation pays no cold
    # initialize.
    proc = FakeAcpProc()
    exec_fn, _created = fake_acp_exec(proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", exec_fn)

    manager = await agent._start_hermes()

    assert isinstance(manager, HermesTaskManager)
    assert isinstance(manager._acp, AcpClient)
    assert manager._acp.available is True
    # initialize was the first request over the eagerly-spawned process.
    assert proc.requests[0]["method"] == "initialize"

    await manager.shutdown()  # acloses the client it owns


@pytest.mark.asyncio
async def test_start_hermes_survives_a_missing_binary(monkeypatch):
    # start() returns False on a missing `hermes` binary — never raises — and the
    # helper still hands back a working (Hermes-less) manager wired to a dead client
    # that will respawn lazily on first use.
    async def _raise(*_args, **_kwargs):
        raise FileNotFoundError("hermes: not found")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise)

    manager = await agent._start_hermes()

    assert isinstance(manager, HermesTaskManager)
    assert isinstance(manager._acp, AcpClient)
    assert manager._acp.available is False

    await manager.shutdown()  # idempotent no-op when nothing spawned


@pytest.mark.asyncio
async def test_start_hermes_swallows_an_unexpected_start_exception(monkeypatch):
    # Belt-and-braces: start() is documented never to raise, but if it ever did,
    # the helper's defensive try/except must swallow it and still return a manager
    # — Hermes must NEVER block worker startup.
    async def _boom(_self):
        raise RuntimeError("unexpected start() explosion")

    monkeypatch.setattr(AcpClient, "start", _boom)

    manager = await agent._start_hermes()

    assert isinstance(manager, HermesTaskManager)
    assert manager._acp.available is False

    await manager.shutdown()
