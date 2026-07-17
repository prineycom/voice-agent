"""Self-tests for the scripted fake `hermes acp` harness (conftest.FakeAcpProc).

These pair the *real* ACP codec (:class:`AcpConnection`) with the fake peer's
stdio streams directly — reader = ``proc.stdout``, writer = ``proc.stdin`` — and
assert the harness's core contract: default ``initialize`` / ``session/new``
answers arrive, a scripted ``session/prompt`` streams its ``session/update``
notifications then a final result, and every inbound request is recorded. This
keeps the harness honest independently of the (separately owned) ``AcpClient``
and ``HermesTaskManager`` tests that consume it.
"""

import asyncio

import pytest

from acp_client import AcpConnection
from conftest import (
    FakeAcpProc,
    acp_agent_message,
    acp_tool_call,
    acp_tool_call_update,
)


def _connect(proc: FakeAcpProc, **kwargs) -> AcpConnection:
    """Wire an AcpConnection over the fake's stdio (its stdout/stdin streams)."""
    return AcpConnection(proc.stdout, proc.stdin, **kwargs)


@pytest.mark.asyncio
async def test_initialize_and_session_new_defaults():
    proc = FakeAcpProc()
    conn = _connect(proc)

    init = await asyncio.wait_for(conn.request("initialize", {"protocolVersion": 1}), 1.0)
    assert init == {"protocolVersion": 1}

    first = await asyncio.wait_for(conn.request("session/new", {"cwd": "/x"}), 1.0)
    second = await asyncio.wait_for(conn.request("session/new", {"cwd": "/y"}), 1.0)
    assert first == {"sessionId": "sess-1"}
    assert second == {"sessionId": "sess-2"}  # unique per call

    # Every inbound request was recorded, in order.
    methods = [r["method"] for r in proc.requests]
    assert methods == ["initialize", "session/new", "session/new"]

    await conn.aclose()


@pytest.mark.asyncio
async def test_scripted_prompt_streams_updates_then_result():
    proc = FakeAcpProc()

    updates: list[tuple[str, dict]] = []
    got_all = asyncio.Event()

    def on_notification(method: str, params: dict) -> None:
        updates.append((method, params))
        # Three scripted updates for this prompt.
        if len(updates) == 3:
            got_all.set()

    conn = _connect(proc, on_notification=on_notification)

    session = await asyncio.wait_for(conn.request("session/new", {"cwd": "/x"}), 1.0)
    sid = session["sessionId"]

    proc.script_prompt(
        session_id=sid,
        updates=[
            acp_tool_call("tc-1", "terminal: uname -a", kind="execute"),
            acp_agent_message("смотрю YouTrack…"),
            acp_tool_call_update("tc-1", status="completed"),
        ],
        result={"stopReason": "end_turn", "answer": "done"},
    )

    result = await asyncio.wait_for(
        conn.request("session/prompt", {"sessionId": sid, "prompt": []}), 1.0
    )

    # The final result resolves the prompt request...
    assert result == {"stopReason": "end_turn", "answer": "done"}
    # ...and by then all three session/update notifications had streamed first.
    await asyncio.wait_for(got_all.wait(), 1.0)
    assert [m for m, _ in updates] == ["session/update"] * 3

    start = updates[0][1]["update"]
    assert start["sessionUpdate"] == "tool_call"
    assert start["title"] == "terminal: uname -a"
    assert start["status"] == "pending"
    assert start["kind"] == "execute"
    finish = updates[2][1]["update"]
    assert finish["sessionUpdate"] == "tool_call_update"
    assert finish["toolCallId"] == "tc-1"
    # The fake injects the sessionId onto each update.
    assert all(params["sessionId"] == sid for _, params in updates)

    # The prompt request was recorded alongside session/new.
    assert [r["method"] for r in proc.requests] == ["session/new", "session/prompt"]

    await conn.aclose()
