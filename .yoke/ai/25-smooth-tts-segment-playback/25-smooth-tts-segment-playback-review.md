# Code Review: 25-smooth-tts-segment-playback

## Summary

### Context and goal

Issue #25 fixes choppy agent speech by adding a streaming TTS path to `DesktopTTS`: a `stream()` returning `DesktopSynthesizeStream` that sentence-tokenizes incoming text and pipelines sentences over ONE persistent WebSocket (reused across turns), draining PCM back in order, with drop-on-cancel barge-in. Gated by a new `tts_streaming` flag (default on). Goal: shorter time-to-first-audio and smoother segment-to-segment playback than the per-utterance one-shot path, which is retained unchanged as a fallback.

### Key code areas for review

1. **`tts_plugin.py:DesktopSynthesizeStream._run()`** — the three-coroutine pipeline (`_feed`/`_send`/`_recv`) over the shared socket; the heart of the change.
2. **`tts_plugin.py:_recv()`** — send/recv rendezvous via a `send_done` Event + `total_sends`/`seen_done` counting; within-message 16-bit alignment carry, now reset at each `done` boundary.
3. **`tts_plugin.py:_drop_ws()` / `_ensure_ws()` / `aclose()`** — persistent-socket lifecycle and barge-in teardown; `aclose()` now holds `_lock`, close handshake is bounded.
4. **`config.py:_env_bool()`** — shared truthy-set env-bool parsing for `TTS_STREAMING` (and now `AGENT_DIAG`).
5. **`agent.py`** — `streaming=cfg.tts_streaming` wiring; ADR-0006 barge-in comment updated.
6. **`tests/conftest.py:FakeTTSServer`** — `loop=True` multi-message server + `hang`/`disconnect` modes.

### Complex decisions

1. **One persistent socket guarded by `asyncio.Lock`** (`tts_plugin.py`) — serializes turns so two streams never interleave on the connection; barge-in drops+reconnects. Correct given the agent speaks one turn at a time.
2. **Single output segment + per-sentence `flush()`** — `start_segment` called exactly once; matches the framework's `_num_segments == 1` guard (`tts.py:492`).
3. **`leftover` carry reset at `done`** (`tts_plugin.py`) — the within-message 16-bit alignment carry is now cleared at each message boundary (real PCM16 messages are even-length), so a malformed odd message can no longer parity-shift all subsequent sentences.

### Questions for the reviewer

1. Confirm on hardware (Pi + Desktop) that the streaming path measurably reduces inter-sentence gaps versus `TTS_STREAMING=false`, and that barge-in stays crisp.
2. For real multi-segment turns the framework feeds one segment per stream instance; if a future change feeds mid-stream flushes, re-verify the `_feed` FlushSentinel path (a framework `push_text`-after-`flush` is currently dropped with a deprecation warning).

### Risks and impact

- `tts_streaming` defaults true → the new path is live by default. The one-shot path is preserved verbatim and used only when `streaming=False`.
- Overlap is playback-buffer overlap; the Desktop `/tts` server stays serial per message (concurrent synthesis of N+1 during N's synthesis is explicitly out of scope).

### Tests and manual checks

**Auto-tests (all green, 70 passed):** two-sentence single-connection pipelining, odd-chunk within-message alignment, ordering/no-loss, error frame → APIError, mid-stream disconnect → APIError, mid-stream flush, barge-in drop-then-reconnect, lock serialization, config default/override, desktop server multi-message contract.

**Manual scenarios:**
1. Long multi-sentence reply on the live rig → smooth playback across sentence boundaries, no audible gaps.
2. Interrupt mid-utterance (barge-in) → speech stops promptly; next turn reconnects cleanly.

### Out of scope

- STT `_drop_ws` unbounded-close (shares the pattern; deferred follow-up).
- Streaming `ws.recv()` timeout and end-of-stream trailing odd-byte drop (accepted; consistent with the one-shot path).
- True server-side concurrent synthesis of the next sentence (would need a `/tts` server change).

## Commits

| Hash      | Description                                                       |
| --------- | ---------------------------------------------------------------- |
| `343731b` | docs: add implementation plan                                    |
| `2f26b22` | feat: add tts_streaming config flag                              |
| `ae4ee30` | test: cover multi-message /tts server contract                   |
| `b9db253` | feat: add streaming TTS path to DesktopTTS                       |
| `e0b9832` | feat: wire tts_streaming flag into agent                         |
| `d24d0a2` | test: cover streaming TTS path and barge-in                      |
| `892b534` | fix: bound TTS socket close to keep barge-in responsive          |
| `920686b` | docs: add execution report                                       |
| `739e3d0` | fix: fix 4 review issues                                         |

## Changed Files

| File                                       | +/-       | Description                                                        |
| ------------------------------------------ | --------- | ----------------------------------------------------------------- |
| `infra/pi/agent/tts_plugin.py`             | +241/-... | Streaming `DesktopSynthesizeStream`, persistent socket, bounded close, leftover reset at `done`, locked `aclose`. |
| `infra/pi/agent/tests/test_tts_plugin.py`  | +222      | Streaming tests: pipelining, odd-chunk alignment, disconnect, flush, barge-in. |
| `infra/pi/agent/tests/conftest.py`         | +102      | `loop=True` multi-message fake `/tts` server + `hang`/`disconnect` modes. |
| `infra/pi/agent/config.py`                 | +18       | `tts_streaming` flag + shared `_env_bool` helper.                 |
| `infra/pi/agent/agent.py`                  | +11/-...  | Wire `streaming=cfg.tts_streaming`; `AGENT_DIAG` via `_env_bool`. |
| `infra/pi/agent/tests/test_config.py`      | +22       | Flag default/override cases.                                      |
| `infra/desktop/tts/tests/test_server.py`   | +34       | Multi-message-per-connection contract test.                       |

## Issues Found

| Severity | Score | Category | File:line                       | Description                                                              |
| -------- | ----- | -------- | ------------------------------- | ------------------------------------------------------------------------ |
| Minor    | 30    | tests    | `tests/test_tts_plugin.py`      | Streaming path lacked mid-stream disconnect and mid-stream flush tests.  |
| Minor    | 28    | bugs     | `tts_plugin.py:283`             | `leftover` PCM byte not reset at `done`; odd message would corrupt later sentences. |
| Minor    | 20    | quality  | `tts_plugin.py:114`             | `aclose()` closed `self._ws` without holding `self._lock` (racy on shutdown). |
| Minor    | 14    | style    | `config.py:78` / `agent.py:240` | Inconsistent bool-env parsing; `bool("false") == True` latent bug in `AGENT_DIAG`. |

## Fixed Issues

| Issue                                         | Commit    | Description                                                                 |
| --------------------------------------------- | --------- | -------------------------------------------------------------------------- |
| Missing streaming disconnect / flush tests    | `739e3d0` | Added `test_streaming_disconnect_midstream_raises_api_error` and `test_streaming_midstream_flush_separate_sentences`. |
| `leftover` not reset at `done`                | `739e3d0` | Reset the carry at each `done` boundary (warn if non-empty); test payloads made even-length with odd intra-message chunks to keep within-message carry covered. |
| `aclose()` not lock-guarded                   | `739e3d0` | `aclose()` now acquires `self._lock` around the socket close (best-effort). |
| Inconsistent bool-env parsing                 | `739e3d0` | Extracted `_env_bool()`; used for both `TTS_STREAMING` and `AGENT_DIAG` (fixes `"false"`→True). |

## Skipped Issues

**All found issues were fixed.**

## Recommendations

- Verify the smoothness win and barge-in latency on the real Pi + Desktop rig; watch the `TTS ttfb` metric (still emitted per response). `TTS_STREAMING=false` reverts to the proven one-shot path if needed.
- Follow-up (out of scope): apply the same bounded-close / lock discipline to `stt_plugin.py:_drop_ws`, and consider a shared persistent-WebSocket helper for the STT and TTS plugins, which now duplicate the lifecycle.
- Run the desktop-tts server suite on the Desktop GPU box as part of CI for that service (it cannot run on the Pi).
