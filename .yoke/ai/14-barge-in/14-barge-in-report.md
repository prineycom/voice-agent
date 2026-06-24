# Report: 14-barge-in

**Plan:** `.yoke/ai/14-barge-in/14-barge-in-plan.md`
**Mode:** sub-agents
**Status:** ✅ complete (offline; live VAD-driven barge-in is the human-gated acceptance)

## Tasks

| #   | Task                                   | Status  | Commit    | Concerns |
| --- | -------------------------------------- | ------- | --------- | -------- |
| 1   | Observable disconnect in FakeTTSServer | ✅ DONE | `c3b5d8b` (+ `b58ebc5` Minor fixes) | — |
| 2   | Barge-in abort-on-cancel tests         | ✅ DONE | `acded81` (+ `b0b5435` review fix) | — |
| 3   | Document barge-in in agent.py (comment) | ✅ DONE | `25c2b9a` | — |
| 4   | Docs — README barge-in section         | ✅ DONE | `35af03a` | — |
| 5   | Validation                             | ✅ pass | —         | live human-gated |

Reviews: T1 ✅ (2 Minor fixed in `b58ebc5`), T2 ✅ after one fix iteration (`b0b5435` — a vacuous assertion was replaced with a frame-count + active-connection leak gauge, both mutation-verified). T3 comment-only / T4 docs — review waived.

## Post-implementation

| Step          | Status   | Commit |
| ------------- | -------- | ------ |
| Validate      | ✅ pass (30 tests) | — |
| Documentation | ⏭️ folded into T3/T4 (`--update-docs` not set) | — |
| Format        | ✅ no formatter configured (matches #11–#13) | — |

## Validation

- Lint / type-check / build: skip (none configured)
- Test: ✅ **30 passed** (28 prior + 2 new barge-in tests)
- Smoke import: ✅ `import agent, …; from livekit.plugins import openai` clean
- **No production logic changed** — `git diff` on `agent.py` is comment/docstring only.

## Key outcome — barge-in is framework-provided

Investigation (verified against installed `livekit-agents==1.6.2`) established that barge-in needs **no new production logic**: interruptions are ON by default (`InterruptionOptions(enabled=True, min_duration=0.5s, false_interruption_timeout=2.0s)`), and the abort chain already reaches our code — a committed interruption cancels the TTS task → `DesktopTTS.ChunkedStream._run`'s `finally: await ws.close()` closes the `/tts` socket → the Desktop server's producer-cancel fires (`infra/desktop/tts/server.py:100-118`) → `clear_buffer()` drops the audio tail. `cancel_and_wait` awaits our close, so no leak.

So this slice is **make-it-explicit + prove-it + document-it**: a discoverability comment in `agent.py`, the abort-on-cancel + no-leak unit tests (the one real coverage gap), and README docs. No server change, no agent logic change.

## Acceptance criteria coverage

1. **Speech stops promptly (socket closes, GPU cancels)** — framework pause→commit + our socket close + server producer-cancel; the close mechanism is proven by `test_cancel_midstream_closes_socket` (the GPU-abort signal). Full VAD path = live acceptance.
2. **New turn handled after interruption** — framework default; live acceptance.
3. **Fresh `/tts` socket per turn** — already true; reaffirmed by `connections` increment in the rapid test + existing `test_websocket_torn_down_per_utterance`.
4. **No audio tail** — framework `clear_buffer`/`clear_queue` on committed interruption; live acceptance (framework-owned, offline-unverifiable).
5. **Rapid interruptions don't wedge/leak** — `test_rapid_interruptions_no_leak`: fresh socket per cycle, bounded time, active-connection gauge drains to 0, plugin works after.

## Changes summary

| File | Action | Description |
| ---- | ------ | ----------- |
| infra/pi/agent/tests/conftest.py | modified | `FakeTTSServer`: observable `disconnected`/`disconnected_event` + `active` connection gauge |
| infra/pi/agent/tests/test_tts_plugin.py | modified | `test_cancel_midstream_closes_socket`, `test_rapid_interruptions_no_leak` |
| infra/pi/agent/agent.py | modified | barge-in discoverability comment + docstring Flow bullet (no logic change) |
| infra/pi/agent/README.md | modified | "Barge-in / interruption" section: behavior, tuning, headphones caveat, live smoke |

## Commits

- `959c029` #14 docs: add implementation plan
- `c3b5d8b` #14 test: make client disconnect observable on FakeTTSServer
- `25c2b9a` #14 docs: document interruption (barge-in) behavior in agent.py
- `b58ebc5` #14 test: hoist asyncio import, clarify disconnect comment
- `acded81` #14 test: cancel-mid-stream closes socket; rapid interruptions no leak
- `35af03a` #14 docs: document barge-in behavior, tuning, and smoke test
- `b0b5435` #14 test: replace vacuous drain check with frame-count + active-connection leak gauge

## Human-gated acceptance (not a build blocker)

The true VAD-driven barge-in — talk over the agent in a live room → speech stops, the new turn is transcribed and answered, no audio tail — needs the live SFU + STT/TTS + LLM + a real mic (as in the #13 live test). All offline portions (abort-on-cancel closes the socket; rapid cancels don't leak; fresh socket per turn) are unit-tested and green.
