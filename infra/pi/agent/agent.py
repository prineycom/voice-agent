"""Agent Worker entrypoint — joins a LiveKit room and speaks a fixed greeting.

This is the Risk-1 go/no-go scaffold: a TTS-only `AgentSession` (NO STT, NO LLM).
On verified livekit-agents 1.6.2, `AgentSession(tts=...)` constructs with only a
TTS provider and `await session.say(text)` performs TTS-only playback without an
LLM — so the direct `rtc.AudioSource` fallback is NOT needed (see SELF_REVIEW).

Flow:
    1. Load config + run the Desktop TTS health gate (abort loudly if down).
    2. Connect to the room.
    3. Start a TTS-only AgentSession with a minimal placeholder Agent.
    4. Wait for a remote participant to join (avoids first-audio clipping),
       then speak the greeting and wait for playout to finish.

Dispatch: this worker registers with an empty `agent_name`, so it is dispatched
automatically to every room (LiveKit default room dispatch). Run it with:
    .venv/bin/python -m agent dev      # dev mode (verbose logs)
    .venv/bin/python -m agent start    # prod mode
See the README (Task 8) for the on-Pi smoke test.
"""

from __future__ import annotations

import logging

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli

from config import load_config
from health import TTSHealthError, check_tts_health
from tts_plugin import DesktopTTS

log = logging.getLogger("agent")

# Placeholder instructions — no LLM is invoked for the fixed greeting, but Agent
# requires an instructions string. Kept short until STT/LLM land in a later task.
AGENT_INSTRUCTIONS = "You are a voice assistant. (Greeting-only scaffold.)"


async def entrypoint(ctx: JobContext) -> None:
    """Health-gate, join the room, and speak the fixed greeting via Desktop TTS."""
    cfg = load_config()

    # Health gate (Risk: a mute agent). Abort before joining if TTS is not ready.
    try:
        await check_tts_health(cfg.tts_health_url)
    except TTSHealthError:
        log.error("Aborting: Desktop TTS health gate failed; not joining the room.")
        raise

    await ctx.connect()

    # TTS-only session — no STT, no LLM. say() drives TTS directly.
    session = AgentSession(
        tts=DesktopTTS(ws_url=cfg.tts_ws_url, voice=cfg.tts_voice),
    )
    await session.start(
        agent=Agent(instructions=AGENT_INSTRUCTIONS),
        room=ctx.room,
    )

    # Gate the greeting on a participant being present/subscribed so the opening
    # words are not clipped (Risk 5). If someone already joined, returns at once.
    participant = await ctx.wait_for_participant()
    log.info("Participant %s joined; speaking greeting.", participant.identity)

    handle = await session.say(cfg.agent_greeting)
    await handle.wait_for_playout()
    log.info("Greeting playout complete.")


# Dispatch is automatic (empty agent_name => room dispatch). The entrypoint runs
# once per assigned room.
if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
