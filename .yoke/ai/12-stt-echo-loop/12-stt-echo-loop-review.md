# Code Review: 12-stt-echo-loop

## Summary

### Context and goal

The #12 slice extends the #11 TTS-only scaffold into a full **STT → echo → TTS loop** on the Pi 5: a custom non-streaming `DesktopSTT` plugin over the Desktop `/stt` WebSocket, LiveKit-owned endpointing (local Silero VAD, `turn_detection="vad"`), and an echo handler that speaks the final transcript back with NO LLM. Reviewed scope was the #12 diff only (`84cd885..HEAD`); the already-merged #11 files were excluded.

### Key code areas for review

1. **`stt_plugin.py:_recognize_impl()`** — the load-bearing path: persistent-socket-per-session under an `asyncio.Lock`, 16 kHz resample, `{"event":"end"}` flush, read-to-`is_final`, `{"error"}`→`APIError`, reset/drop on failure. Verified against the installed `livekit-agents==1.6.2` non-streaming STT contract.
2. **`agent.py:entrypoint()`** — dual health gate before connect; local `silero.VAD.load()`; `AgentSession` with no `llm`; echo via `user_input_transcribed` → `session.say`.
3. **`health.py:_check_health()`** — generalized gate; `check_stt_health`/`STTHealthError` added; TTS public API preserved.
4. **`tests/conftest.py:FakeSTTServer`** + **`tests/test_stt_plugin.py`** — real-socket behavioral tests incl. the one-socket-per-session guard.

### Complex decisions

1. **Non-streaming STT + framework `StreamAdapter`** (`stt_plugin.py`, DD-1) — the default streaming node never flushes per turn; `StreamAdapter` calls `recognize()` once per VAD `END_OF_SPEECH`, the exact endpointing hook ADR-0006 requires.
2. **One persistent socket per session** (`stt_plugin.py`, DD-2) — held as instance state + lock; `end` flushes+clears, `reset`/drop discards. Diverges deliberately from the TTS fresh-socket-per-utterance pattern.
3. **Echo with no LLM** (`agent.py`, DD-4) — the turn pipeline returns early when `llm is None` (`agent_activity.py:2231`), so nothing competes with `session.say()`.
4. **Drop-socket-on-cancel** (`stt_plugin.py`, review fix) — on `CancelledError` (barge-in) the socket is closed and reconnected next turn (fresh server buffer), instead of relying on an in-band reset that the cancellation may interrupt.

### Questions for the reviewer

1. With `turn_detection="vad"` and a single user track, confirm the agent's own TTS output never re-enters its STT input (no self-echo) in the real room — covered logically (STT listens to the user track only), pending live confirmation.
2. Is the per-Pi `STT_LANGUAGE` label-only behavior acceptable, or should recognition language be made controllable from the Pi in a later slice?

### Risks and impact

- **Live acoustic loop unverified** — unit coverage and local VAD load are green; the speak-and-hear-echo path needs the Desktop STT/TTS services + a room + mic (manual acceptance).
- **Production retries re-upload the buffer** — `recognize()` `max_retry=3` re-sends the full utterance after a reset; functionally correct, documented as a cost note.

### Tests and manual checks

**Auto-tests:** 19 passing — STT framing/resample/`end`, one-socket-across-turns guard, error→`APIError`, reset-on-failure, **mid-turn disconnect → recover (connections==2)**, **interim-partials skipped**, STT health ok/degraded/refused. Real ephemeral-port fake servers, no GPU/SFU.

**Manual scenarios:**
1. `curl $STT_HEALTH_URL` and `$TTS_HEALTH_URL` → 200 + `model_loaded:true`.
2. Start the worker; speak a Russian phrase → LiveKit ends the turn → plugin flushes `end` → agent echoes it back through TTS, hands-free, within one turn.
3. `systemctl restart` → recovers.

### Out of scope

- LLM / real conversation (#13), barge-in/interruption (#14), Hermes (#15), TLS edge (#9).
- All `infra/desktop/**` (prior epics).

## Commits

| Hash    | Description |
| ------- | ----------- |
| 4f7dc2e | docs(12-stt-echo-loop): add implementation plan |
| bc8ba87 | chore(12-stt-echo-loop): add Silero VAD dependency |
| af8e5b3 | feat(12-stt-echo-loop): add STT config env vars |
| a038adf | refactor(12-stt-echo-loop): generalize health gate for STT |
| 1db3e95 | test(12-stt-echo-loop): add fake STT server fixtures |
| 4e071fa | feat(12-stt-echo-loop): add STT WebSocket plugin |
| f2c9aa8 | test(12-stt-echo-loop): add STT health gate tests |
| 189fd30 | test(12-stt-echo-loop): add STT plugin tests |
| 69e3e7b | feat(12-stt-echo-loop): wire STT echo loop into agent worker |
| 379476d | docs(12-stt-echo-loop): document STT echo loop and env vars |
| 00fae8a | docs(12-stt-echo-loop): add execution report |
| 113cc04 | fix(12-stt-echo-loop): fix 7 review issues |

## Changed Files

| File | +/- | Description |
| ---- | --- | ----------- |
| infra/pi/agent/stt_plugin.py | +177 | `DesktopSTT` plugin (persistent socket, 16k resample, mono guard, drop-on-cancel) |
| infra/pi/agent/agent.py | +83/-? | STT echo loop wiring (dual gate, local VAD, `turn_detection="vad"`, no LLM) |
| infra/pi/agent/health.py | +82/-? | Generalized health gate + STT |
| infra/pi/agent/config.py | +18 | STT env vars |
| infra/pi/agent/requirements.txt | +3 | Silero VAD dep |
| infra/pi/agent/tests/conftest.py | +121 | `FakeSTTServer` fixtures |
| infra/pi/agent/tests/test_stt_plugin.py | +193 | Plugin tests incl. disconnect-recover + partials |
| infra/pi/agent/tests/test_health.py | +35 | STT health gate tests |
| infra/pi/agent/.env.example | +6 | STT vars |
| infra/pi/agent/README.md | +113/-? | STT echo loop docs |

## Issues Found

| Severity | Score | Category | File:line | Description |
| -------- | ----- | -------- | --------- | ----------- |
| Important | 70 | documentation | .env.example + README (`STT_LANGUAGE`) | Docs claimed `STT_LANGUAGE` is "passed to the server"; it only tags `SpeechData.language` — server uses its own env |
| Important | 55 | tests | test_stt_plugin.py | Mid-turn disconnect path untested (fake server supported it) |
| Minor | 45 | tests | test_stt_plugin.py | Interim-partial (`is_final:false`) skip path untested |
| Minor | 40 | bugs | stt_plugin.py:88-98 | Resampler hardcoded mono; stereo frame would be mis-resampled (no guard) |
| Minor | 30 | bugs | stt_plugin.py:118-126 | On `CancelledError` the in-band reset could be cut off → stale server buffer / `_ws` not cleared |
| Minor | 15 | documentation | requirements.txt vs README | Silero weights size figure inconsistent (~2 MB vs ~1 MB) |
| Minor | 15 | performance | stt_plugin.py | `recognize()` retries re-upload the full buffer (benign; undocumented) |

## Fixed Issues

| Issue | Commit | Description |
| ----- | ------ | ----------- |
| `STT_LANGUAGE` misleading docs (Important) | `113cc04` | Reworded: it labels `SpeechData.language` only; recognition language is set on the Desktop server |
| Disconnect path untested (Important) | `113cc04` | Added `test_disconnect_raises_then_recovers`: `APIError` + `_ws is None` + reconnect (`connections==2`) |
| Partials untested (Minor) | `113cc04` | Added `test_interim_partials_skipped`: returns the FINAL transcript, not a partial |
| Mono resampler assumption (Minor) | `113cc04` | Explicit mono guard raising a descriptive `APIError` on non-mono frames |
| Reset on cancellation (Minor) | `113cc04` | On `CancelledError`, drop+close the socket (clean reconnect next turn); keep best-effort reset for non-cancel errors |
| Silero size figure (Minor) | `113cc04` | Unified to "~1–2 MB" in both docs |
| Retry re-upload note (Minor) | `113cc04` | Added a code comment documenting the retry re-upload cost |

## Skipped Issues

**All found issues were fixed.**

(One pre-recorded Minor — `FakeSTTServer` clears its buffer after an error frame whereas the real server keeps it — was excluded as a known, accepted test-fidelity gap.)

## Recommendations

- The drop-socket-on-cancel fix is directly relevant to the next slice (#14 barge-in); verify it under real interruption there.

## Live verification (on-Pi e2e)

Ran the full echo loop on the Pi against the live SFU + Desktop STT + Desktop TTS. The test client synthesizes a known Russian phrase via the Desktop TTS, publishes it into the room as its "microphone" track, and records whatever the agent publishes back.

- **Dependencies up:** Desktop STT `/health` 200 `model_loaded:true` (large-v3-turbo, CUDA); Desktop TTS `/health` 200 (Qwen3, Russian); SFU on :7880; worker registered (`AW_…`), Silero plugin loaded locally.
- **Transcription:** STT returned the spoken phrase verbatim (`user_transcript: "Один, два, три. Как слышно?"`, `language: ru`) — accurate, no rate-mismatch garble (confirms the 16 kHz resample, DD-5).
- **Echo playout:** the agent spoke the transcript back — captured audio shows two distinct speech bursts (greeting at ~1–5 s, echo at ~10.5–14 s); log shows the assistant `conversation_item_added` for the echoed text. Clean teardown (`session closed error=null`).

### Bug found and fixed live (`4984967`)

The first run transcribed perfectly but **the echo never played** — `session.say()` called from a `user_input_transcribed` event callback races the turn commit and is dropped (with no LLM the turn pipeline returns early). Fixed by moving the echo into `EchoAgent.on_user_turn_completed`, which the framework awaits inside the turn pipeline *before* the `llm is None` short-circuit, so the scheduled `say()` plays. Re-verified live: echo audible. The unit suite (19 passing) did not catch this because it tests the plugin in isolation, not the full `AgentSession` echo wiring — a coverage gap noted for follow-up.
