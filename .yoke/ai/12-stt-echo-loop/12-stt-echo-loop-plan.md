# STT echo loop (input path + LiveKit turn detection) — implementation plan

**Task:** GitHub issue #12 (Epic 4 — Agent Worker on Pi 5)
**Complexity:** medium
**Mode:** sub-agents
**Parallel:** true

## Design decisions

### DD-1: Non-streaming `DesktopSTT` + framework `StreamAdapter` (not a hand-rolled streaming `RecognizeStream`)

**Decision:** Implement `DesktopSTT(stt.STT)` with `STTCapabilities(streaming=False, interim_results=False)` and rely on the framework's auto-wrap.
**Rationale:** Verified against installed `livekit-agents==1.6.2`: `voice/agent.py:430-437` auto-wraps any STT whose `capabilities.streaming=False` in `stt.StreamAdapter(stt=..., vad=activity.vad)`; `stt/stream_adapter.py:115-127` drives the VAD and on `VADEventType.END_OF_SPEECH` calls `self._wrapped_stt.recognize(buffer=merged_frames, ...)` exactly once per turn. That is precisely the per-turn end-of-turn hook ADR-0006 requires (LiveKit owns endpointing).
**Alternative:** A hand-rolled streaming `RecognizeStream` — rejected: the default streaming `stt_node` (`voice/agent.py:421-466`) never calls `stream.flush()` per turn, so a streaming plugin gets no clean turn boundary and would have to override `stt_node`.

### DD-2: One persistent STT socket per session, held as instance state, serialized by `asyncio.Lock`

**Decision:** Open `websockets.connect(ws_url, max_size=None)` lazily on the first `_recognize_impl`, store it on the `DesktopSTT` instance, reuse it across turns, close it in `aclose()`. An `asyncio.Lock` serializes turns onto the shared server buffer. Per turn under the lock: resample buffer→16k, send PCM16 binary frames, send `{"event":"end"}`, read frames until `{"is_final": true}` (skip `partial` frames), return the FINAL `SpeechEvent`. On exception/cancel inside a turn, send best-effort `{"event":"reset"}` so a half-buffer never leaks into the next turn, then re-raise as `APIError`.
**Rationale:** ADR-0006 mandates one STT socket per session; `end` flushes+clears the server buffer (`infra/desktop/stt/server.py:137-142`), `reset` discards (`server.py:143-145`). Contrast with the TTS plugin, which opens a fresh socket per utterance (`tts_plugin.py:80`).
**Alternative:** Fresh socket per turn — rejected: violates ADR-0006 and re-pays connect cost each turn.

### DD-3: LiveKit Silero VAD owns endpointing; `turn_detection="vad"`

**Decision:** Construct `AgentSession(..., vad=silero.VAD.load(), turn_detection="vad")`. The plugin sends `{"event":"end"}` only when the StreamAdapter (driven by Silero VAD `END_OF_SPEECH`) calls `recognize()`.
**Rationale:** ADR-0006 "double VAD by design" — LiveKit VAD for turn control (Pi) + Desktop `vad_filter` for audio cleaning (`server.py:103`); the server never auto-endpoints. The default `inference.TurnDetector()` probes LiveKit Cloud and 401s on the self-hosted Pi (root cause of #11's `turn_detection="manual"`).
**Alternative:** Default turn detection — rejected: Cloud 401 on the self-hosted Pi.

### DD-4: Echo via `user_input_transcribed` event → `session.say` (NO LLM)

**Decision:** Build `AgentSession` with `stt`, `tts`, `vad` and NO `llm`. Subscribe `@session.on("user_input_transcribed")`; on `ev.is_final` with a non-empty transcript, call `session.say(ev.transcript)`.
**Rationale:** Verified at `voice/agent_activity.py:2231-2232` — the turn pipeline returns early (`elif self.llm is None: return`) before any reply generation when no LLM is set, so no session-generated reply competes with our `say()`. `session.say()` needs only TTS (`agent_session.py:1166-1198`). Event fields `transcript: str` / `is_final: bool` verified at `voice/events.py:314-318`.
**Alternative:** Override `Agent.llm_node` to echo the last user message — rejected: `llm_node` is never reached when `self.llm is None`, so it would require constructing a dummy `llm.LLM` purely to pass the guards.

### DD-5: Resample to 16 kHz mono with `rtc.AudioResampler`

**Decision:** Merge the buffer (`utils.merge_frames` = `rtc.combine_audio_frames`); if `frame.sample_rate != 16000`, push through one `rtc.AudioResampler(input_rate, 16000, num_channels=1, quality=HIGH)`, concatenating `push()` + `flush()` outputs into the PCM16 bytestream. Recreate the resampler when the input rate changes.
**Rationale:** Requirement #6 (no rate-mismatch garble); the server fixes 16 kHz (`server.py:61`). LiveKit room frames are commonly 48 kHz.
**Alternative:** Assume input is already 16 kHz — rejected: would garble transcripts.

### DD-6: Generalize the health gate, preserve the TTS API

**Decision:** Refactor `health.py` to a private `_check_health(url, service_name, error_cls, timeout)` and expose `check_stt_health` + `STTHealthError`, keeping `check_tts_health`/`TTSHealthError` unchanged (delegating).
**Rationale:** Requirement #5 (STT health gate alongside TTS). The STT `/health` returns the identical shape (200 + truthy `model_loaded`, else 503; `server.py:84-94`), so the same logic applies verbatim. The existing `health_server` test fixture is already generic and reusable.
**Alternative:** Copy-paste a second function — rejected: duplicated logic.

## Tasks

### Task 1: Add Silero VAD dependency

- **Files:** `infra/pi/agent/requirements.txt` (edit)
- **Depends on:** none
- **Scope:** S
- **What:** Add `livekit-plugins-silero==1.6.2` to requirements.
- **How:** Add the pinned line; add a short comment noting aarch64/py3.13 wheel + onnxruntime backend and that Silero downloads model weights on first load.
- **Context:** `infra/pi/agent/requirements.txt` (full file).
- **Verify:** `.venv/bin/pip install -r requirements.txt` resolves on the Pi (aarch64/py3.13); `python -c "from livekit.plugins import silero"` imports.

### Task 2: Config — STT env vars

- **Files:** `infra/pi/agent/config.py` (edit)
- **Depends on:** none
- **Scope:** S
- **What:** Add `stt_ws_url`, `stt_health_url`, `stt_sample_rate` (16000), `stt_language` ("ru") to `AgentConfig` + `load_config`.
- **How:** Mirror the TTS env pattern; int-parse `STT_SAMPLE_RATE` with the same validation guard as `TTS_SAMPLE_RATE`. Defaults: `STT_WS_URL=ws://100.75.88.35:8001/stt`, `STT_HEALTH_URL=http://100.75.88.35:8001/health`, `STT_LANGUAGE=ru`. Confirm port 8001 against `infra/desktop/stt/server.py:55-62`.
- **Context:** `infra/pi/agent/config.py:16-72`; `infra/desktop/stt/server.py:55-62`.
- **Verify:** `python -c "from config import load_config"` parses with the new fields present.

### Task 3: STT plugin

- **Files:** `infra/pi/agent/stt_plugin.py` (create)
- **Depends on:** none
- **Scope:** L
- **What:** Implement `DesktopSTT(stt.STT)` per DD-1/DD-2/DD-5.
- **How:** `__init__(*, ws_url, language="ru", sample_rate=16000)`; `capabilities=STTCapabilities(streaming=False, interim_results=False)`. Lazy persistent `websockets` connection + `asyncio.Lock`. `_recognize_impl(buffer, *, language=NOT_GIVEN, conn_options)`: `merge_frames(buffer)` → resample to 16k (DD-5) → send PCM16 binary frames → send `{"event":"end"}` → read frames until `{"is_final": true}` (skip `partial`) → return `SpeechEvent(type=SpeechEventType.FINAL_TRANSCRIPT, alternatives=[SpeechData(language=lang, text=...)])`. On `{"error":...}` raise `APIError`; on exception/cancel send best-effort `{"event":"reset"}` then re-raise as `APIError`. `aclose()` closes the socket. Keep `ws_url` injectable for tests. Mirror `tts_plugin.py` structure (odd-byte handling not needed inbound, but mirror error/lifecycle conventions).
- **Context:** `infra/pi/agent/tts_plugin.py` (full — blueprint); `.venv/.../livekit/agents/stt/stt.py:53-254` (`STT`, `_recognize_impl`, `SpeechEvent`, `SpeechData`, `STTCapabilities`); `.venv/.../livekit/agents/utils/audio.py:17-20` (`AudioBuffer`, `merge_frames`); `.venv/.../livekit/rtc/audio_resampler.py:78,126`; `infra/desktop/stt/server.py:107-154` (wire contract).
- **Verify:** `python -c "import stt_plugin"` imports; covered by Task 6.

### Task 4: Generalized health gate

- **Files:** `infra/pi/agent/health.py` (edit)
- **Depends on:** none
- **Scope:** M
- **What:** Add `STTHealthError` + `check_stt_health`; refactor a shared `_check_health(url, service_name, error_cls, timeout)`; keep `check_tts_health`/`TTSHealthError` intact (delegating).
- **How:** Extract the existing TTS logic into `_check_health`; `check_tts_health` and `check_stt_health` become thin wrappers passing service name + error class. Same gate key: HTTP 200 + truthy `model_loaded`.
- **Context:** `infra/pi/agent/health.py:26-60`; `infra/desktop/stt/server.py:84-94`.
- **Verify:** `python -c "from health import check_stt_health, STTHealthError, check_tts_health"`; covered by Task 7.

### Task 5: FakeSTTServer test fixtures

- **Files:** `infra/pi/agent/tests/conftest.py` (edit)
- **Depends on:** none
- **Scope:** M
- **What:** Add `FakeSTTServer` + `stt_server_factory` fixtures mirroring the TTS fixtures.
- **How:** Real `websockets.serve` on `127.0.0.1:0` path `/stt`: append binary frames to a buffer (record byte count), parse text control frames; on `{"event":"end"}` emit `{"text":<canned>,"is_final":true,"partial":""}` then clear; on `{"event":"reset"}` clear (no emit); optional `partial` frames; `error` and `disconnect` modes; expose a `connections` counter and `url = ws://127.0.0.1:{port}/stt`. Factory tears all servers down.
- **Context:** `infra/pi/agent/tests/conftest.py:29-131` (`FakeTTSServer`, `tts_server_factory`, `health_server`).
- **Verify:** `pytest tests/ -q --co` collects without import error.

### Task 6: STT plugin tests

- **Files:** `infra/pi/agent/tests/test_stt_plugin.py` (create)
- **Depends on:** Task 3, Task 5
- **Scope:** L
- **What:** Behavioral tests mirroring `test_tts_plugin.py`.
- **How:** Assert: server receives well-formed PCM16 @16k for a turn; control event order (`end`); emitted `SpeechEvent` carries the canned transcript with `language="ru"`; resampling path (feed a 48k frame, assert server byte-count ≈ downsampled); `{"error":...}` → `APIError`; `reset` sent on error/cancel; **exactly ONE socket across two sequential turns** (`srv.connections == 1`); `aclose()` closes it.
- **Context:** `infra/pi/agent/tests/test_tts_plugin.py:23-128`; the Task 3 plugin contract; the Task 5 fixtures.
- **Verify:** `.venv/bin/python -m pytest tests/test_stt_plugin.py -q` — green.

### Task 7: STT health gate tests

- **Files:** `infra/pi/agent/tests/test_health.py` (edit)
- **Depends on:** Task 4
- **Scope:** S
- **What:** Add STT health-gate tests reusing the generic `health_server`.
- **How:** ok → returns body; degraded (503) → `STTHealthError`; connection refused → `STTHealthError`; assert a clear error message.
- **Context:** `infra/pi/agent/tests/test_health.py` (full); `infra/pi/agent/tests/conftest.py:96-131` (`health_server`).
- **Verify:** `.venv/bin/python -m pytest tests/test_health.py -q` — green.

### Task 8: Agent wiring

- **Files:** `infra/pi/agent/agent.py` (edit)
- **Depends on:** Task 2, Task 3, Task 4
- **Scope:** L
- **What:** Wire STT into the entrypoint: health gate, plugin, VAD, turn detection, and echo handler.
- **How:** Add the STT health gate beside the TTS gate before `ctx.connect()`. Build `DesktopSTT(ws_url=cfg.stt_ws_url, language=cfg.stt_language, sample_rate=cfg.stt_sample_rate)` and `vad = silero.VAD.load()` (load in `prewarm`/before session start). Construct `AgentSession(stt=..., tts=..., vad=vad, turn_detection="vad")` (no `llm`). Register `@session.on("user_input_transcribed")` → `if ev.is_final and ev.transcript.strip(): session.say(ev.transcript)`. Remove the now-obsolete fixed greeting/`turn_detection="manual"` as appropriate; update the module docstring (no longer TTS-only).
- **Context:** `infra/pi/agent/agent.py` (full); `.venv/.../livekit/agents/voice/events.py:314-318` (`user_input_transcribed` fields); `.venv/.../livekit/agents/voice/agent_session.py:1166-1198` (`say`).
- **Verify:** `python -c "import agent"` imports; logged STT health gate + `turn_detection="vad"` wiring present.

### Task 9: Docs — env example + README

- **Files:** `infra/pi/agent/.env.example` (edit), `infra/pi/agent/README.md` (edit)
- **Depends on:** Task 2, Task 8
- **Scope:** M
- **What:** Document the new STT env vars and the echo-loop smoke test.
- **How:** Add `STT_WS_URL`, `STT_HEALTH_URL`, `STT_SAMPLE_RATE`, `STT_LANGUAGE` to `.env.example` and the README config table. Add an echo-loop smoke test section ("speak a Russian phrase, hear it echoed back, hands-free, no LLM"). Note the Silero first-run model download. Update the README intro (no longer TTS-only).
- **Context:** `infra/pi/agent/README.md` (config table + smoke test sections); `infra/pi/agent/.env.example` (full).
- **Verify:** Manual read — all four vars documented; smoke test present.

### Task 10: Validation

- **Files:** —
- **Depends on:** all
- **Scope:** M
- **What:** Run the full test suite and confirm all six acceptance criteria are covered.
- **How:** `.venv/bin/python -m pytest tests/ -q`; static import check that `from livekit.plugins import silero` resolves on aarch64/py3.13. Flag any wheel/onnxruntime resolution failure as a blocker.
- **Context:** —
- **Verify:** `.venv/bin/python -m pytest tests/ -q` — all green; `silero` import resolves.

## Execution

- **Mode:** sub-agents
- **Parallel:** true
- **Reasoning:** Ten atomic tasks across disjoint files; Wave 1 (config/deps/plugin/health/fixtures) has no file intersections and parallelizes cleanly, while `agent.py` wiring depends on plugin+health+config.
- **Order:**
  Group 1 (parallel): Task 1, Task 2, Task 3, Task 4, Task 5
  ─── barrier ───
  Group 2 (parallel): Task 6 (needs Task 3, Task 5), Task 7 (needs Task 4)
  ─── barrier ───
  Group 3 (sequential): Task 8 → Task 9
  ─── barrier ───
  Group 4: Task 10

## Verification

Acceptance criteria (from issue #12):

1. Custom STT plugin connects to `/stt`, streams mic audio as PCM16 @16kHz, and consumes `{text, is_final, partial}` — Task 3, Task 6.
2. AgentSession VAD + turn detection drives endpointing; the plugin sends `{"event":"end"}` only on LiveKit's turn-end signal — DD-1/DD-3, Task 3, Task 8.
3. A single STT socket persists for the session; `end` clears the buffer per turn and `reset` discards a turn — DD-2, Task 3, Task 6.
4. Spoken Russian phrases are transcribed and spoken back (echo) within one turn, hands-free — DD-4, Task 8, Task 9 smoke test.
5. Startup health-gates STT `/health`; an unavailable STT yields a clear logged error — DD-6, Task 4, Task 7, Task 8.
6. Mic audio is correctly downsampled to 16kHz — DD-5, Task 3, Task 6.

## Materials

- ADR-0006 — `docs/adr/0006-agent-turn-control.md` (binding turn-control decisions).
- Desktop STT server — `infra/desktop/stt/server.py` (the real wire contract).
- #11 prior art — `infra/pi/agent/tts_plugin.py`, `health.py`, `config.py`, `tests/conftest.py`.
