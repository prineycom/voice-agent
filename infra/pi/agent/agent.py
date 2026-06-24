"""Agent Worker entrypoint — STT → echo → TTS loop (NO LLM).

This wires both audio paths through the Desktop GPU boxes: the Desktop STT
plugin transcribes each user turn, and the Desktop TTS plugin speaks it back
verbatim. There is NO LLM in the loop — the echo handler simply repeats the
final transcript, which exercises input + output end to end before reasoning
lands in a later task.

Turn-taking uses a LOCAL Silero VAD (`silero.VAD.load()`, ONNX run on the Pi)
with `turn_detection="vad"`. This is deliberate: the framework's default VAD is
cloud-backed and 401s on a self-hosted Pi (the very failure that forced
`turn_detection="manual"` in #11). A DeprecationWarning from
livekit-plugins-silero is expected on the pinned 1.6.2 and is harmless.

Flow:
    1. Load config + run the Desktop STT and TTS health gates (abort loudly if
       either is down — a deaf or mute agent must never join a room).
    2. Connect to the room.
    3. Start an AgentSession with STT + TTS + local VAD (no LLM).
    4. Greet once when a remote participant joins, then echo every final
       user transcript back via TTS.

Dispatch: this worker registers with an empty `agent_name`, so it is dispatched
automatically to every room (LiveKit default room dispatch). Run it with:
    .venv/bin/python -m agent dev      # dev mode (verbose logs)
    .venv/bin/python -m agent start    # prod mode
See the README (Task 8) for the on-Pi smoke test.
"""

from __future__ import annotations

import logging

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli, llm
from livekit.plugins import silero

from config import load_config
from health import (
    STTHealthError,
    TTSHealthError,
    check_stt_health,
    check_tts_health,
)
from stt_plugin import DesktopSTT
from tts_plugin import DesktopTTS

log = logging.getLogger("agent")

# Placeholder instructions — no LLM is invoked in the echo loop, but Agent
# requires an instructions string. Kept short until the LLM lands in a later task.
AGENT_INSTRUCTIONS = "You are a voice assistant. (STT echo-loop scaffold, no LLM.)"


class EchoAgent(Agent):
    """Echoes each completed user turn back through TTS — no LLM.

    `on_user_turn_completed` is the correct extension point: the framework
    awaits it inside the turn pipeline *before* its `if llm is None: return`
    short-circuit (see livekit.agents.voice.agent_activity), so a `say()`
    scheduled here is played as the turn's response. Calling `say()` from a
    `user_input_transcribed` event callback instead races the turn commit and
    the speech is dropped.
    """

    def __init__(self) -> None:
        super().__init__(instructions=AGENT_INSTRUCTIONS)

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        text = new_message.text_content
        if text and text.strip():
            log.info("Echoing transcript: %s", text)
            self.session.say(text)


async def entrypoint(ctx: JobContext) -> None:
    """Health-gate, join the room, and echo each user turn via STT → TTS."""
    cfg = load_config()

    # Health gates (Risk: a deaf or mute agent). Abort before joining if either
    # Desktop service is not ready. Run both with the same loud-abort discipline.
    try:
        await check_stt_health(cfg.stt_health_url)
    except STTHealthError:
        log.error("Aborting: Desktop STT health gate failed; not joining the room.")
        raise
    try:
        await check_tts_health(cfg.tts_health_url)
    except TTSHealthError:
        log.error("Aborting: Desktop TTS health gate failed; not joining the room.")
        raise

    # Load the LOCAL Silero VAD once before constructing the session. This runs
    # the ONNX model on the Pi (no cloud), so turn detection never hits LiveKit
    # Cloud inference and never 401s. A DeprecationWarning here is expected.
    vad = silero.VAD.load()

    await ctx.connect()  # type: ignore[call-arg]

    # STT + TTS session with local VAD; turn_detection="vad" uses the loaded
    # Silero VAD to bound user turns. No LLM — the echo handler drives say().
    session = AgentSession(
        stt=DesktopSTT(
            ws_url=cfg.stt_ws_url,
            language=cfg.stt_language,
            sample_rate=cfg.stt_sample_rate,
        ),
        tts=DesktopTTS(
            ws_url=cfg.tts_ws_url,
            voice=cfg.tts_voice,
            sample_rate=cfg.tts_sample_rate,
        ),
        vad=vad,
        turn_detection="vad",
    )

    # The echo (NO LLM) is driven by EchoAgent.on_user_turn_completed, which runs
    # inside the turn pipeline at the correct point (see EchoAgent docstring).
    await session.start(
        agent=EchoAgent(),
        room=ctx.room,
    )

    # Gate the greeting on a participant being present/subscribed so the opening
    # words are not clipped (Risk 5). If someone already joined, returns at once.
    participant = await ctx.wait_for_participant()
    log.info("Participant %s joined; speaking greeting.", participant.identity)

    session.say(cfg.agent_greeting)
    log.info("Greeting spoken; entering STT echo loop.")


# Dispatch is automatic (empty agent_name => room dispatch). The entrypoint runs
# once per assigned room.
if __name__ == "__main__":
    # Pass credentials explicitly from the loaded .env: the livekit CLI does not
    # read .env itself, so a manual foreground run would otherwise miss the keys
    # (under systemd they arrive via EnvironmentFile).
    cfg = load_config()
    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        ws_url=cfg.livekit_url,
        api_key=cfg.livekit_api_key,
        api_secret=cfg.livekit_api_secret,
        # The worker's own HTTP server port. The framework prod default (8081) is
        # already taken on the Pi; AGENT_WORKER_PORT (default 8090) avoids the clash.
        port=cfg.worker_port,
    ))
