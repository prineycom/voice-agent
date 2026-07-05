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
    5. Interruption (barge-in): if the user speaks over the agent, the
       framework stops TTS → DesktopTTS closes the /tts socket → Desktop
       server cancels its producer; a new turn begins (see ADR-0006).

Dispatch: this worker registers with an empty `agent_name`, so it is dispatched
automatically to every room (LiveKit default room dispatch). Run it with:
    .venv/bin/python -m agent dev      # dev mode (verbose logs)
    .venv/bin/python -m agent start    # prod mode
See the README (Task 8) for the on-Pi smoke test.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable

from livekit.agents import NOT_GIVEN, Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.agents import metrics as agent_metrics
from livekit.agents.voice.events import MetricsCollectedEvent
from livekit.plugins import openai, silero

from config import _env_bool, load_config
from health import (
    STTHealthError,
    TTSHealthError,
    check_stt_health,
    check_tts_health,
)
from hermes_tasks import UI_TOPIC
from motion_events import DEFAULT_EMOTION, EmotionTagStripper, motion_event_json
from stt_plugin import DesktopSTT
from tts_plugin import DesktopTTS
from worker_tools import (
    cancel_hermes_tasks,
    delegate_to_hermes,
    list_hermes_tasks,
    make_hermes_manager,
    run_command,
)

log = logging.getLogger("agent")


class GreetingAgent(Agent):
    """Agent that speaks a fixed greeting as soon as it enters the session.

    on_enter is the livekit-agents lifecycle hook that fires once the agent is
    active in the AgentSession (room connected, IO wired). Speaking the greeting
    here — instead of awaiting ctx.wait_for_participant() in the entrypoint —
    avoids a fragile wait that hung in practice while the participant was already
    present; RoomIO delivers the audio to the connected participant.
    """

    def __init__(
        self,
        *,
        instructions: str,
        tools: list,
        greeting: str,
        publish_motion: Callable[[bytes], None] | None = None,
    ) -> None:
        super().__init__(instructions=instructions, tools=tools)
        self._greeting = greeting
        # Authoritative motion publisher (ADR-0009): emits motion/expression
        # events on the voiceagent data channel. None disables publishing.
        self._publish_motion = publish_motion
        # Current expression, parsed from inline LLM emotion tags in llm_node;
        # the state-change hook in the entrypoint pairs it with the motion state.
        self.current_emotion = DEFAULT_EMOTION
        # Current motion state, mirrored from the framework's agent_state_changed
        # hook. Emotion-driven events reuse it so a tag parsed mid-generation (often
        # while still "thinking") doesn't flip the avatar to "speaking" prematurely.
        self.current_state = "initializing"

    async def on_enter(self) -> None:
        log.info("Agent entered session; speaking greeting.")
        self.session.say(self._greeting)

    async def llm_node(self, chat_ctx, tools, model_settings):
        """Strip inline emotion tags from the LLM stream, driving expressions.

        The LLM emits inline ``[emotion:xxx]`` tags in its reply (ADR-0009). They
        must never reach TTS or the transcript, so this override runs every text
        delta through EmotionTagStripper: complete tags are removed and each parsed
        emotion both updates ``self.current_emotion`` and publishes a "speaking"
        motion event. Non-text chunks (tool calls) pass through untouched.
        """
        self.current_emotion = DEFAULT_EMOTION

        def _on_emotion(emotion: str) -> None:
            self.current_emotion = emotion
            if self._publish_motion:
                # Pair the expression with the agent's *current* motion state, not a
                # literal "speaking": tags are parsed during generation, often before
                # TTS begins, so hardcoding "speaking" would flip the pose too early.
                self._publish_motion(motion_event_json(self.current_state, emotion))

        stripper = EmotionTagStripper(on_emotion=_on_emotion)
        async for chunk in super().llm_node(chat_ctx, tools, model_settings):
            # Only transform text deltas; pass tool-call / non-text chunks through
            # untouched (they have no .delta.content to strip).
            delta = getattr(chunk, "delta", None)
            if delta is not None and getattr(delta, "content", None):
                cleaned = stripper.feed(delta.content)
                # Drop the chunk only if it's now empty AND carries no other payload:
                # a delta can hold a tool_call alongside its text, and dropping the
                # whole chunk would silently lose that co-located tool call.
                if not cleaned and not getattr(delta, "tool_calls", None):
                    continue  # fully-consumed text (held back / all tag) -> drop it
                delta.content = cleaned
                yield chunk
            else:
                yield chunk
        # Emit any text held back at end-of-stream (a dangling partial tag).
        tail = stripper.flush()
        if tail:
            yield tail


def _load_text_file(path: Path, *, what: str, required: bool = True) -> str | None:
    """Read a UTF-8 text file used in the Agent instructions (SOUL, worker skill).

    `required=True` fails loud on missing/empty (SOUL.md — no personality, abort).
    `required=False` warns and returns None so the worker still boots (the skill
    is an enhancement, not a startup gate).
    """
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        if required:
            raise RuntimeError(
                f"{what} file not found at {path}. It is loaded as part of the "
                "agent's system prompt; create it or set the *_PATH env var."
            )
        log.warning("%s file not found at %s; continuing without it.", what, path)
        return None
    except (PermissionError, IsADirectoryError, OSError) as exc:
        # Same actionable fail-loud discipline as a missing SOUL: a path that
        # exists but can't be read (wrong perms, a directory, I/O error) is a
        # startup-fatal misconfiguration, not a silent skip.
        raise RuntimeError(
            f"{what} file at {path} could not be read ({exc.__class__.__name__}: {exc}). "
            "Check the *_PATH env var points to a readable file."
        ) from exc
    if not text:
        if required:
            raise RuntimeError(
                f"{what} file at {path} is empty. It is loaded verbatim as part of "
                "the agent's system prompt; populate it before starting."
            )
        log.warning("%s file at %s is empty; continuing without it.", what, path)
        return None
    return text


def _load_soul(path: Path) -> str:
    """Read the SOUL personality file (UTF-8) for use as the LLM system prompt.

    Fail loud (same discipline as load_config's missing-key error): a blank or
    missing SOUL means the agent has no personality, so abort with an
    actionable message rather than joining the room with empty instructions.
    """
    return _load_text_file(path, what="SOUL", required=True) or ""


async def entrypoint(ctx: JobContext) -> None:
    """Health-gate, join the room, and run the STT → LLM → TTS pipeline."""
    cfg = load_config()
    soul_text = _load_soul(cfg.soul_path)
    # Worker skill (Hermes CLI patterns): optional, appended to the instructions.
    # Not a startup gate — a missing skill just means the LLM lacks the run_command
    # pattern docs (run_command itself is still registered; the LLM may still try).
    skill_text = _load_text_file(cfg.worker_skill_path, what="Worker skill", required=False)
    instructions = soul_text if not skill_text else f"{soul_text}\n\n{skill_text}"

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

    # Background Hermes delegation manager (async tool calls). Stored in the
    # session userdata so the function_tool adapters reach it; cancelled on
    # shutdown so a user disconnect never leaves orphan Hermes subprocesses.
    hermes_manager = make_hermes_manager()
    ctx.add_shutdown_callback(hermes_manager.shutdown)
    # Stream tool/background-task events to the web UI as LiveKit data messages
    # (topic UI_TOPIC); the frontend renders the live operations panel + tool feed.
    #
    # reliable=False (lossy) is deliberate: transcription forwarding and these UI
    # data messages share LiveKit's ORDERED reliable data channel to the browser.
    # A single bulky UI message (e.g. a Hermes "done" event carrying tool output)
    # that stalls on the Tailscale path head-of-line-blocks that channel, and the
    # transcript silently freezes behind it while audio keeps flowing (issue #23).
    # The lossy channel is a separate SCTP stream, so a stalled/dropped UI message
    # can never wedge the transcript. UI data is non-critical: the task snapshot
    # self-corrects on the next publish and a tool result is also spoken.
    hermes_manager.set_publisher(
        lambda data: ctx.room.local_participant.publish_data(
            data, reliable=False, topic=UI_TOPIC
        )
    )

    # Authoritative motion-event publisher (ADR-0009): the agent is the single
    # source of truth for the avatar's motion state. State changes (below) and
    # inline-emotion parsing (GreetingAgent.llm_node) both publish through this on
    # the same UI_TOPIC data channel the frontend already consumes.
    # publish_data is a coroutine; these call sites are sync event-loop callbacks,
    # so schedule it as a task (awaiting inline isn't possible) and swallow any
    # failure inside the task so a dropped publish can never abort the turn.
    def publish_motion(payload: bytes) -> None:
        async def _send() -> None:
            try:
                # lossy (reliable=False) for the same reason as the Hermes UI
                # publisher above: never share the transcript's ordered reliable
                # channel, so a motion event can never head-of-line-block it (#23).
                await ctx.room.local_participant.publish_data(
                    payload, reliable=False, topic=UI_TOPIC
                )
            except Exception:
                log.exception("failed to publish motion event")

        asyncio.create_task(_send())

    # Full STT → LLM → TTS session with local VAD; turn_detection="vad" uses the
    # loaded Silero VAD to bound user turns. The LLM is the local LiteLLM proxy
    # spoken to via the OpenAI-compatible plugin (base_url must carry the /v1
    # suffix, supplied by config).
    #
    # Barge-in (interruption): handled entirely by livekit-agents defaults —
    # there is NO explicit barge-in code here by design (ADR-0006).
    # In 1.6.2 the defaults are: InterruptionOptions(enabled=True,
    # min_duration=0.5 s, false_interruption_timeout=2.0 s).
    # On a committed interruption the framework cancels the active TTS task;
    # DesktopTTS._run's finally-block closes the /tts WebSocket; the Desktop
    # TTS server cancels its producer on disconnect (no in-band stop message,
    # per ADR-0006); locally-buffered audio is dropped via the framework's
    # clear_buffer.  In the streaming path (streaming=True) the persistent TTS
    # socket is dropped and reconnected on the next stream, so interruption
    # still means "close the TTS WebSocket" — preserving the ADR-0006 contract
    # (no in-band stop).  To tune, pass:
    #   turn_handling=TurnHandlingOptions(interruption=InterruptionOptions(...))
    # Do NOT use the deprecated flat kwargs allow_interruptions= /
    # min_interruption_* — they were removed in 1.6.x.
    # Hoisted so the entrypoint can wire the voiceagent publisher + emotion source
    # after the agent is constructed (the streaming path forwards re-based A2F
    # blendshape frames and one reply-level {"done": true} through the publisher,
    # and reads the current emotion at each sentence flush).
    desktop_tts = DesktopTTS(
        ws_url=cfg.tts_ws_url,
        voice=cfg.tts_voice,
        sample_rate=cfg.tts_sample_rate,
        streaming=cfg.tts_streaming,
    )

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
        tts=desktop_tts,
        vad=vad,
        turn_detection="vad",
        userdata=hermes_manager,
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

    # Authoritative motion source (ADR-0009): the framework's agent-state changes
    # (initializing/listening/thinking/speaking) drive the avatar's motion state.
    # The emotion is the latest expression parsed from the inline LLM tags in
    # llm_node, so each motion event carries the current state + expression.
    @session.on("agent_state_changed")
    def _on_agent_state(ev) -> None:
        # Keep the agent's tracked state current first, so emotion-driven events
        # parsed in llm_node pair their expression with the right motion state.
        agent.current_state = ev.new_state
        publish_motion(motion_event_json(ev.new_state, agent.current_emotion))

    # --- Diagnostics for the "transcript stops on long output" bug (issue under
    # investigation). These are cheap, high-signal hooks: which conversation items
    # actually get committed (and their length), and a loud log if the session closes
    # with an error. Gated behind AGENT_DIAG (default off).
    # TODO(#23): remove once the transcript-wedge fix is confirmed on live hardware.
    diag_enabled = _env_bool("AGENT_DIAG", default=False)
    if diag_enabled:
        @session.on("conversation_item_added")
        def _on_item(ev) -> None:
            item = getattr(ev, "item", None)
            role = getattr(item, "role", "?")
            text = getattr(item, "text_content", None) or ""
            log.info("diag: conversation_item role=%s len=%d", role, len(text))

        @session.on("close")
        def _on_close(ev) -> None:
            log.error("diag: session close reason=%s error=%r",
                      getattr(ev, "reason", "?"), getattr(ev, "error", None))

    # Greet from on_enter (the documented livekit-agents pattern) rather than
    # awaiting ctx.wait_for_participant(): in a live test that helper hung even
    # though the participant was present and its mic was already being read, so
    # the greeting never played. on_enter fires once the agent is active in the
    # session; RoomIO routes the audio to the connected participant.
    agent = GreetingAgent(
        instructions=instructions,
        tools=[delegate_to_hermes, cancel_hermes_tasks, list_hermes_tasks, run_command],
        greeting=cfg.agent_greeting,
        publish_motion=publish_motion,
    )

    # Wire the TTS plugin to the voiceagent data channel now that the agent (and
    # its current_emotion) exists. The streaming path forwards re-based A2F
    # blendshape frames and one reply-level {"done": true} through publish_motion
    # (same lossy UI_TOPIC channel), and reads the current emotion at each flush.
    desktop_tts.set_publisher(publish_motion)
    desktop_tts.set_emotion_source(lambda: agent.current_emotion)

    await session.start(
        agent=agent,
        room=ctx.room,
    )
    log.info("Session started; greeting on agent enter, then STT → LLM → TTS loop.")


# Dispatch is automatic (empty agent_name => room dispatch). The entrypoint runs
# once per assigned room.
if __name__ == "__main__":
    # Pass credentials explicitly from the loaded .env: the livekit CLI does not
    # read .env itself, so a manual foreground run would otherwise miss the keys
    # (under systemd they arrive via EnvironmentFile).
    cfg = load_config()
    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        num_idle_processes=1,
        ws_url=cfg.livekit_url,
        api_key=cfg.livekit_api_key,
        api_secret=cfg.livekit_api_secret,
        # The worker's own HTTP server port. The framework prod default (8081) is
        # already taken on the Pi; AGENT_WORKER_PORT (default 8090) avoids the clash.
        port=cfg.worker_port,
    ))
