# Report: 25-smooth-tts-segment-playback

**Plan:** `.yoke/ai/25-smooth-tts-segment-playback/25-smooth-tts-segment-playback-plan.md`
**Mode:** sub-agents
**Status:** ✅ complete

## Tasks

| #   | Task                                          | Status  | Commit    | Concerns |
| --- | --------------------------------------------- | ------- | --------- | -------- |
| 1   | Add `tts_streaming` config flag               | ✅ DONE | `2f26b22` | —        |
| 2   | Streaming TTS path in `tts_plugin.py`         | ✅ DONE | `b9db253` | —        |
| 3   | Wire the flag in `agent.py`                   | ✅ DONE | `e0b9832` | —        |
| 4   | Streaming test infra + tests                  | ✅ DONE | `d24d0a2` | resolved — see below |
| 5   | Desktop server multi-message contract test    | ✅ DONE | `ae4ee30` | —        |
| 6   | Validation                                    | ✅ pass | —         | —        |

## Post-implementation

| Step          | Status        | Commit    |
| ------------- | ------------- | --------- |
| Validate      | ✅ pass        | —         |
| Documentation | ⏭️ skipped     | —         |
| Format        | ⏭️ skipped (none configured) | — |

## Concerns

### Task 4: streaming barge-in close under backpressure

The streaming-path tests surfaced a robustness asymmetry: when the fake server floods PCM far faster than real time and the consumer has stopped (barge-in), `_drop_ws()` → `await ws.close()` could block on the websockets close handshake for the full close-timeout (~10s). A reviewer reproduced it empirically (10.01s under a 64 MiB unpaced flood vs 0.02s on the one-shot path). Classified **Minor** — production Desktop GPU TTS streams roughly real-time (~48 KB/s), so only a bounded backlog is ever in flight and `close()` returns immediately.

**Resolution:** Applied the recommended cheap hardening — bounded the close handshake with `asyncio.wait_for(ws.close(), timeout=2.0)` in `DesktopTTS._drop_ws` (commit `892b534`), so interruption stays responsive regardless of server-side backpressure; the socket is forgotten either way.

> Follow-up (out of scope): the identical pattern exists in `stt_plugin.py:_drop_ws`. Apply the same bound there in a later pass for consistency.

## Validation

- Lint: ⏭️ skip — no linter configured (no `pyproject.toml` / `ruff` / `Makefile` in `infra/pi/agent`).
- Type-check: ⏭️ skip — none configured.
- Test (agent worker): ✅ `infra/pi/agent/.venv/bin/python -m pytest tests/ -q` → **68 passed**, 1 unrelated deprecation warning.
- Test (desktop tts): ✅ runs on the Desktop GPU box. Verified by the implementer in an ad-hoc light venv → `tests/test_server.py` 8 passed (7 prior + 1 new). The Desktop service venv is not present on the Pi.
- Build: N/A.

## Changes summary

| File                                          | Action   | Description |
| --------------------------------------------- | -------- | ----------- |
| `infra/pi/agent/config.py`                    | modified | Add `tts_streaming` field + `TTS_STREAMING` env parse (default true). |
| `infra/pi/agent/tts_plugin.py`                | modified | Add streaming `DesktopSynthesizeStream` over a persistent socket with pipelined sentence sends, drop-on-cancel barge-in, bounded close. One-shot `ChunkedStream` path untouched. |
| `infra/pi/agent/agent.py`                     | modified | Pass `streaming=cfg.tts_streaming`; note streaming barge-in in the ADR-0006 comment. |
| `infra/pi/agent/tests/conftest.py`            | modified | Fake `/tts` server `loop=True` (multi-message/one socket) + `hang` mode + per-index chunks. |
| `infra/pi/agent/tests/test_tts_plugin.py`     | modified | 4 streaming tests: one-connection pipelining, ordering, error→APIError, barge-in drop+reconnect. |
| `infra/pi/agent/tests/test_config.py`         | modified | Default-true + `TTS_STREAMING=false` cases. |
| `infra/desktop/tts/tests/test_server.py`      | modified | Multi-message-per-connection contract test. |

## Commits

- `343731b` #25 docs(25-smooth-tts-segment-playback): add implementation plan
- `2f26b22` #25 feat(25-smooth-tts-segment-playback): add tts_streaming config flag
- `ae4ee30` #25 test(25-smooth-tts-segment-playback): cover multi-message /tts server contract
- `b9db253` #25 feat(25-smooth-tts-segment-playback): add streaming TTS path to DesktopTTS
- `e0b9832` #25 feat(25-smooth-tts-segment-playback): wire tts_streaming flag into agent
- `d24d0a2` #25 test(25-smooth-tts-segment-playback): cover streaming TTS path and barge-in
- `892b534` #25 fix(25-smooth-tts-segment-playback): bound TTS socket close to keep barge-in responsive

## Notes

- The fix is **flag-guarded** (`TTS_STREAMING`, default on). Set `TTS_STREAMING=false` to fall back to the proven one-shot `StreamAdapter` path without a code change.
- **Verification on hardware:** the smoothness win is best confirmed by listening on a real session and watching the `TTS ttfb` metric (still emitted per response). The true overlap is playback-buffer overlap — the Desktop `/tts` server remains serial per message; concurrent synthesis of N+1 during N's synthesis is explicitly out of scope (would need a server change).
