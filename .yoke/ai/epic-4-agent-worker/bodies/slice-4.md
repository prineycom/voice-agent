## Parent

[Epic 4] Agent Worker on Pi 5 — https://github.com/prineycom/voice-agent/issues/4

## What to build

**Barge-in (Interruption)**: the user can speak over the agent and the agent stops
talking and listens. LiveKit's VAD detects the user's speech while the agent is
responding; the Agent Worker aborts in-flight TTS by **closing the TTS WebSocket**,
which triggers the GPU Worker's existing producer-cancel (there is no in-band stop
message in the `/tts` protocol). The agent then handles the new user turn normally.

This realizes the interruption half of ADR-0006 and depends on the per-turn TTS
socket lifecycle (a fresh `/tts` socket per turn, so closing it is a clean abort).

Decisions this realizes (from grilling + ADR-0006):
- **Interruption aborts TTS by closing the socket** — no in-band cancel needed.
- **Fresh TTS socket per turn** (vs the per-session STT socket).
- In-flight TTS audio already buffered locally is dropped, not played out.

## Acceptance criteria

- [ ] Speaking over the agent stops its speech promptly (TTS socket closes, GPU producer cancels)
- [ ] After an interruption the agent processes the new user turn correctly (STT → LLM → TTS resumes)
- [ ] Each agent turn uses a fresh `/tts` socket so closing it cleanly aborts only that turn
- [ ] No audio "tail" plays after an interruption (locally buffered chunks are discarded)
- [ ] Rapid back-to-back interruptions don't wedge the pipeline or leak sockets/tasks

## Blocked by

- #13 (Slice 3 — LiteLLM proxy + real conversation)
