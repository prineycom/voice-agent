# Report: 12-stt-echo-loop

**Plan:** `.yoke/ai/12-stt-echo-loop/12-stt-echo-loop-plan.md`
**Mode:** sub-agents
**Status:** ✅ complete

## Tasks

| #   | Task                              | Status  | Commit    | Concerns |
| --- | --------------------------------- | ------- | --------- | -------- |
| 1   | Add Silero VAD dependency         | ✅ DONE | `bc8ba87` | —        |
| 2   | Config — STT env vars             | ✅ DONE | `af8e5b3` | —        |
| 3   | STT WebSocket plugin              | ✅ DONE | `4e071fa` | —        |
| 4   | Generalized health gate           | ✅ DONE | `a038adf` | —        |
| 5   | FakeSTTServer test fixtures       | ✅ DONE | `1db3e95` | see below |
| 6   | STT plugin tests                  | ✅ DONE | `189fd30` | —        |
| 7   | STT health gate tests             | ✅ DONE | `f2c9aa8` | —        |
| 8   | Agent wiring (STT echo loop)      | ✅ DONE | `69e3e7b` | —        |
| 9   | Docs — env example + README       | ✅ DONE | `379476d` | —        |
| 10  | Validation                        | ✅ pass | —         | —        |

All 8 implementation tasks passed combined spec+quality review (T1 trivial dep line — review waived; T9 docs — review waived). Verdicts: T2 ✅, T3 ✅, T4 ✅, T5 ✅, T6 ✅, T8 ✅.

## Post-implementation

| Step          | Status   | Commit |
| ------------- | -------- | ------ |
| Validate      | ✅ pass  | —      |
| Documentation | ⏭️ skipped (folded into T9; `--update-docs` not set) | — |
| Format        | ✅ no formatter configured (matches #11) | — |

## Concerns

### Task 5: FakeSTTServer test fixtures

Reviewer noted one Minor spec-fidelity gap: in `error` mode the fake server clears its buffer after sending the error frame, whereas the real Desktop server keeps the buffer after a per-message error. Non-blocking — `error`-mode tests are single-turn and never observe the stale buffer. Recorded, not fixed (Minor).

## Validation

- Lint: skip (no linter configured at package or repo root — consistent with #11)
- Type-check: skip (none configured)
- Test: ✅ `.venv/bin/python -m pytest tests/ -q` → **17 passed** (8 from #11 + 6 STT plugin + 3 STT health)
- Build: skip (N/A for this package)
- Smoke import: ✅ `import agent, config, health, stt_plugin, tts_plugin` clean (silero `DeprecationWarning` + ONNX GPU-discovery warnings are expected/harmless on the Pi)
- Dependency resolution (de-risked early): ✅ `livekit-plugins-silero==1.6.2` + `onnxruntime-1.27.0` resolved on aarch64/py3.13 (cp313 manylinux aarch64 wheel); `silero.VAD.load()` loads the model **locally** (no cloud) — clears the T1/T10 wheel risk.

## Changes summary

| File                                | Action   | Description |
| ----------------------------------- | -------- | ----------- |
| infra/pi/agent/requirements.txt     | modified | Add `livekit-plugins-silero==1.6.2` (local Silero VAD; onnxruntime backend) |
| infra/pi/agent/config.py            | modified | Add `stt_ws_url`, `stt_health_url`, `stt_sample_rate`, `stt_language` |
| infra/pi/agent/stt_plugin.py        | created  | `DesktopSTT(stt.STT)` — non-streaming adapter over `/stt`, one persistent socket per session + `asyncio.Lock`, 16 kHz resample, `end`/`reset`, error→`APIError` |
| infra/pi/agent/health.py            | modified | Generalized to `_check_health` + `check_stt_health`/`STTHealthError`; TTS API preserved |
| infra/pi/agent/agent.py             | modified | STT echo loop: dual health gate, local `silero.VAD.load()`, `turn_detection="vad"`, no LLM, `user_input_transcribed`→`session.say` |
| infra/pi/agent/tests/conftest.py    | modified | `FakeSTTServer` + `stt_server_factory` fixtures |
| infra/pi/agent/tests/test_stt_plugin.py | created | 6 behavioral tests incl. the one-socket-per-session guard |
| infra/pi/agent/tests/test_health.py | modified | 3 STT health-gate tests |
| infra/pi/agent/.env.example         | modified | Four STT vars |
| infra/pi/agent/README.md            | modified | STT echo loop + dual health gate + Silero/STT prerequisites |

## Commits

- `4f7dc2e` #12 docs(12-stt-echo-loop): add implementation plan
- `bc8ba87` #12 chore(12-stt-echo-loop): add Silero VAD dependency
- `af8e5b3` #12 feat(12-stt-echo-loop): add STT config env vars
- `a038adf` #12 refactor(12-stt-echo-loop): generalize health gate for STT
- `1db3e95` #12 test(12-stt-echo-loop): add fake STT server fixtures
- `4e071fa` #12 feat(12-stt-echo-loop): add STT WebSocket plugin
- `f2c9aa8` #12 test(12-stt-echo-loop): add STT health gate tests
- `189fd30` #12 test(12-stt-echo-loop): add STT plugin tests
- `69e3e7b` #12 feat(12-stt-echo-loop): wire STT echo loop into agent worker
- `379476d` #12 docs(12-stt-echo-loop): document STT echo loop and env vars

## Key design decisions realized

- **DD-1** Non-streaming `DesktopSTT` + framework `StreamAdapter` + VAD — the default streaming `stt_node` never flushes per turn; `StreamAdapter` calls `recognize()` once per VAD `END_OF_SPEECH`, the exact endpointing hook ADR-0006 requires.
- **DD-2** One persistent socket per session held as instance state + `asyncio.Lock`; `end` flushes+clears, `reset` discards (verified by the one-socket-across-turns test).
- **DD-3** `turn_detection="vad"` + **local** `silero.VAD.load()` — deliberately avoids the cloud-backed default VAD that would 401 on the self-hosted Pi (the #11 lesson).
- **DD-4** Echo with NO LLM via `user_input_transcribed` → `session.say` — the turn pipeline returns early when `llm is None` (`agent_activity.py:2231`), so nothing competes with `say()`.
- **DD-5** 16 kHz resample via `rtc.AudioResampler` (room frames are commonly 48 kHz).
- **DD-6** Generalized health gate; TTS public API unchanged.

## Not verified here (deferred to live smoke)

Live on-Pi e2e (speak a Russian phrase → hear it echoed, hands-free, within one turn) requires the Desktop STT/TTS services + a LiveKit room and a real microphone client. Unit coverage and the local VAD load are green; the acoustic loop is the manual acceptance step (README smoke test).
