"""Agent Worker entrypoint — STT → LLM → TTS pipeline with a SOUL personality.

This wires a full conversational loop through the Desktop GPU boxes and the
local LiteLLM proxy: the Desktop STT plugin transcribes each user turn, the
`openai.LLM` plugin (pointed at the on-Pi LiteLLM `/v1` endpoint) reasons over
it under the SOUL.md system prompt, and the Desktop TTS plugin speaks the
reply. SOUL.md is the agent's personality (Russian by design) and is loaded
verbatim as the system prompt at startup.

Turn-taking uses a LOCAL Silero VAD (`silero.VAD.load()`, ONNX run on the Pi)
with `turn_detection="vad"`. This is deliberate: the framework's default VAD is
cloud-backed and 401s on a self-hosted Pi (the very failure that forced
`turn_detection="manual"` in #11). A DeprecationWarning from
livekit-plugins-silero is expected on the pinned 1.6.2 and is harmless.

Flow:
    1. Load config + the SOUL personality, then run the Desktop STT and TTS
       health gates (abort loudly if either is down — a deaf or mute agent must
       never join a room).
    2. Connect to the room.
    3. Start an AgentSession with STT + LLM + TTS + local VAD, instructed by
       SOUL.md, and register a first-audio latency log hook.
    4. Greet once when a remote participant joins, then run the full
       STT → LLM → TTS conversation loop.

Dispatch: this worker registers with an empty `agent_name`, so it is dispatched
automatically to every room (LiveKit default room dispatch). Run it with:
    .venv/bin/python -m agent dev      # dev mode (verbose logs)
    .venv/bin/python -m agent start    # prod mode
See the README (Task 8) for the on-Pi smoke test.
"""

from __future__ import annotations

import logging
from pathlib import Path

from livekit.agents import NOT_GIVEN, Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.agents import metrics as agent_metrics
from livekit.agents.voice.events import MetricsCollectedEvent
from livekit.plugins import openai, silero

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


def _load_soul(path: Path) -> str:
    """Read the SOUL personality file (UTF-8) for use as the LLM system prompt.

    Fail loud (same discipline as load_config's missing-key error): a blank or
    missing SOUL means the agent has no personality, so abort with an
    actionable message rather than joining the room with empty instructions.
    """
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"SOUL file not found at {path}. Create infra/pi/agent/SOUL.md "
            "or set SOUL_PATH to a readable file — it is loaded as the agent's "
            "system prompt."
        ) from exc
    if not text:
        raise RuntimeError(
            f"SOUL file at {path} is empty. It is loaded verbatim as the agent's "
            "system prompt; populate it with the personality before starting."
        )
    return text


async def entrypoint(ctx: JobContext) -> None:
    """Health-gate, join the room, and run the STT → LLM → TTS pipeline."""
    cfg = load_config()
    soul_text = _load_soul(cfg.soul_path)

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

    # Full STT → LLM → TTS session with local VAD; turn_detection="vad" uses the
    # loaded Silero VAD to bound user turns. The LLM is the local LiteLLM proxy
    # spoken to via the OpenAI-compatible plugin (base_url must carry the /v1
    # suffix, supplied by config).
    session = AgentSession(
        stt=DesktopSTT(
            ws_url=cfg.stt_ws_url,
            language=cfg.stt_language,
            sample_rate=cfg.stt_sample_rate,
        ),
        llm=openai.LLM(
            model=cfg.llm_model,
            base_url=cfg.llm_base_url,
            api_key=cfg.llm_api_key,
            # Disable model "thinking" for voice latency. Passed only when set;
            # the plugin auto-detects effort only for known OpenAI models, so a
            # custom LiteLLM alias needs it explicit. NOT_GIVEN omits it.
            reasoning_effort=cfg.llm_reasoning_effort or NOT_GIVEN,
        ),
        tts=DesktopTTS(
            ws_url=cfg.tts_ws_url,
            voice=cfg.tts_voice,
            sample_rate=cfg.tts_sample_rate,
        ),
        vad=vad,
        turn_detection="vad",
    )

    # First-audio latency (req #6): log LLM time-to-first-token and TTS
    # time-to-first-byte as turns complete. `metrics_collected` is deprecated in
    # 1.6.2 (forward path: ChatMessage.metrics) but is the simplest reliable
    # latency hook for this slice. The event carries an AgentMetrics union on
    # `.metrics`; we isinstance-discriminate the LLM vs TTS variants.
    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent) -> None:
        m = ev.metrics
        if isinstance(m, agent_metrics.LLMMetrics):
            log.info("first-audio-latency: LLM ttft=%.3fs", m.ttft)
        elif isinstance(m, agent_metrics.TTSMetrics):
            log.info("first-audio-latency: TTS ttfb=%.3fs", m.ttfb)

    await session.start(
        agent=Agent(instructions=soul_text),
        room=ctx.room,
    )

    # Gate the greeting on a participant being present/subscribed so the opening
    # words are not clipped (Risk 5). If someone already joined, returns at once.
    participant = await ctx.wait_for_participant()
    log.info("Participant %s joined; speaking greeting.", participant.identity)

    session.say(cfg.agent_greeting)
    log.info("Greeting spoken; entering STT → LLM → TTS loop.")


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
