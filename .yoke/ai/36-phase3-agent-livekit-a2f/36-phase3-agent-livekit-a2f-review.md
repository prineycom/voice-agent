# Code Review: 36-phase3-agent-livekit-a2f

## Summary

### Context and goal

Epic 8 Phase 3 multiplexes NVIDIA Audio2Face ARKit blendshape frames from the
Desktop `/tts` service to the browser via the Pi agent on the `voiceagent`
DataChannel, routes the LLM emotion into each `/tts` request, and preserves
barge-in. Desktop tees each sentence's PCM into a best-effort A2F fork behind a
single-writer outbound queue; the agent demuxes PCM→AudioEmitter and re-bases
each blendshape's `t` to reply-relative before forwarding, emitting exactly one
reply-level `{"done":true}` per reply.

### Key code areas for review

1. **`infra/pi/agent/tts_plugin.py:_recv()`** — the PCM/blendshapes/`done`/`a2f_done` demux, the reply-relative `t` re-basing, and the `seen_done`/`seen_a2f_done` exit gate.
2. **`infra/desktop/tts/server.py:tts_ws()`** — single-writer queue, the per-sentence `audio_done` gate, `on_done`→`_emit` `a2f_done` ordering, and the fork-None fallback marker.
3. **`infra/desktop/tts/a2f_fork.py:A2FFork`** — `_fire_done` once-only semantics and the guarded `on_frame` drain loop.
4. **`infra/pi/agent/agent.py:entrypoint()`** — hoisting `DesktopTTS` to wire `set_publisher(publish_motion)` + `set_emotion_source`.

### Complex decisions

1. **Completed forks run concurrently, not awaited** (`server.py:253`) — pipelines A2F across sentences for lower latency, but means sentence N's `a2f_done`/blendshapes can be produced by an independent A2F session while sentence N+1 already streams. This is precisely the interleaving that Issue #1 (now fixed) addressed on the plugin side via a per-sentence duration FIFO.
2. **Duration accounting via a per-sentence FIFO** (`tts_plugin.py`, post-fix) — pushes each finalized sentence duration at audio `{"done"}` and pops it at the matching `a2f_done`, so `reply_offset_s` advances by the correct sentence's duration regardless of wire interleaving.

### Questions for the reviewer

1. The Desktop guarantees exactly one `a2f_done` per sentence but not that sentence N's `a2f_done` precedes sentence N+1's audio `done`. The plugin FIFO now tolerates any interleaving — is there any remaining desire to also enforce ordering server-side (e.g. bound how far A2F can lag), or is apply-on-arrival + the FIFO sufficient?

### Risks and impact

- Lip-sync timing (not audio) is the residual risk surface; the measure-first ADR-0013 gate (mouth lead/lag) is still an open live-e2e task.
- Cross-service coupling: the agent's normal-reply exit hard-requires one `a2f_done` per sentence. A fork that connects then hangs forever (never sends `done`/`error`) would emit no marker and stall a normal reply — pre-existing in `a2f_fork.py`, does not reproduce with the real A2F server, barge-in unaffected. Recommended follow-up: an `asyncio.wait_for` timeout around the fork recv.

### Tests and manual checks

**Auto-tests:**

- Agent suite `cd infra/pi/agent && .venv/bin/python -m pytest tests/ -q` → **102 passed**, 1 pre-existing unrelated deprecation warning.
- Desktop fork/server tests run green on the Pi via an ad-hoc venv (GPU module stubbed): `test_a2f_fork.py` 8 passed; `test_server_fork.py`+`test_server.py` green across repeated runs.
- New regression tests: out-of-order `a2f_done` `t`-rebasing, malformed-frame audio survival, emotion threading, monotonic re-basing, one reply-`{done}`, publish-failure isolation, A2F-absent isolation, barge-in.

**Manual scenarios:**

1. Multi-sentence, emotion-tagged reply on the real Desktop+agent+browser → face tracks speech; one `voiceagent {done}` per reply; barge-in decays the face; `?lipsync=volume` retreat works.
2. Capture the `mouth.js` `crossCorrelateOffset` "mouth sync offset: N ms" log across several utterances → decide apply-on-arrival vs adaptive delay vs volume retreat (ADR-0013, threshold lead ≤ ~45 ms / lag ≤ ~125 ms).

### Out of scope

- The frontend consumer (`blendshapes.js`/`mouth.js`, shipped in #35) and the A2F NIM server itself.
- Enabling/tuning the adaptive delay loop beyond the measure-first decision.
- The optional `a2f_fork.py` recv-timeout hardening (recommended follow-up).

## Commits

| Hash | Description |
| ---- | ----------- |
| `4b63bb6` | feat: forward A2F blendshape frames from the TTS fork |
| `8e92f22` | fix: guard on_frame callback so a raising consumer cannot kill the A2F drain loop |
| `4d69b7c` | feat: demux A2F blendshapes and route emotion in the agent TTS plugin |
| `1a9440f` | feat: wire voiceagent publisher and emotion source into DesktopTTS |
| `db778be` | feat: multiplex A2F blendshapes onto /tts via a single-writer queue |
| `7214841` | fix: fix 2 review issues (t-rebasing FIFO + malformed-frame guard) |

## Changed Files

| File | +/- | Description |
| ---- | --- | ----------- |
| infra/desktop/tts/a2f_fork.py | +41/− | Forward A2F frames via `on_frame`; suppress A2F `{done}`; `on_done()` once in `finally`; guard `on_frame` against raising consumers |
| infra/desktop/tts/server.py | +101/− | Single-writer outbound queue; PCM/`done` reliable, blendshapes drop-on-full; per-sentence gated `a2f_done` (fork + no-fork fallback) |
| infra/desktop/tts/tests/test_a2f_fork.py | +131/− | Verbatim forwarding, `on_done` exactly-once, raising-`on_frame` robustness |
| infra/desktop/tts/tests/test_server.py | +12/− | Tolerate interleaved `a2f_done`/`blendshapes` frames |
| infra/desktop/tts/tests/test_server_fork.py | +83/− | Forwarded blendshapes + exactly-one `a2f_done` (incl. A2F-down) |
| infra/pi/agent/agent.py | +26/− | Hoist `DesktopTTS`; inject publisher + emotion source |
| infra/pi/agent/tests/conftest.py | +134/− | `FakeTTSServer` blendshapes + `a2f_done` + reorder mode + publisher spy |
| infra/pi/agent/tests/test_tts_plugin.py | +314/− | Full Phase-3 behavioral coverage incl. 2 review regressions |
| infra/pi/agent/tts_plugin.py | +183/− | Demux + reply-relative `t` (FIFO) + emotion routing + publisher/emotion setters + one reply-`{done}` + malformed-frame guard |

## Issues Found

| Severity | Score | Category | File:line | Description |
| -------- | ----- | -------- | --------- | ----------- |
| Important | 52 | bugs | `infra/pi/agent/tts_plugin.py:416-421` / `infra/desktop/tts/server.py:253` | Reply-relative `t` used a single overwritten `last_sentence_dur_s`; out-of-order background A2F fork completion (server does not await completed forks) advanced `reply_offset_s` by the wrong sentence's duration, skewing every later blendshape `t`. |
| Minor | 34 | bugs | `infra/pi/agent/tts_plugin.py:405-414` | Unguarded `data["frame"]/["t"]/["arkit"]` on a blendshape frame → `KeyError`/`TypeError` propagates to the reply's `except` → `_drop_ws()`, killing the reply's audio for a cosmetic frame (violates "A2F never breaks audio"). |

## Fixed Issues

| Issue | Commit | Description |
| ----- | ------ | ----------- |
| `t`-rebasing skew under out-of-order `a2f_done` | `7214841` | Replaced the single `last_sentence_dur_s` with a `collections.deque` FIFO: push each sentence's finalized duration at audio `{done}`, pop the oldest at the matching `a2f_done` (empty-guarded to never raise). Pairs each `a2f_done` with its own sentence's duration regardless of interleaving. Regression test emits sentence 1's `a2f_done` after sentence 2's `done` and asserts unskewed monotonic `t`. |
| Malformed blendshape frame kills reply audio | `7214841` | Wrapped frame extraction/construction in `try/except (KeyError, TypeError)` that skips the frame (debug log), so a malformed frame drops like a failed publish and never surfaces to the drain loop. Regression test feeds frames missing `t`/`arkit` and asserts audio completes + one reply-`{done}`. |

## Skipped Issues

**All found issues were fixed.**

The 5 Minor items already recorded and accepted in the execution report (`_swallow_task` `Exception` vs `BaseException`; inline sync-publisher blocking; one-shot-path emotion; `test_server.py` collector relaxation; the fork connect-then-hang follow-up) were passed to the reviewer as known/excluded and are not re-listed here.

## Recommendations

- **Live-e2e measure-first gate (ADR-0013):** capture the `mouth.js` `crossCorrelateOffset` offset on the real stack and record the apply-on-arrival vs adaptive-delay decision. This is the last open item of the Definition of Done.
- **Follow-up hardening:** add an `asyncio.wait_for` timeout around the A2F fork's blendshape recv loop in `a2f_fork.py`, so a connect-then-hang A2F session cannot stall a normal reply now that the agent's exit depends on one `a2f_done` per sentence.
- **Desktop test run:** re-run `infra/desktop/tts/tests/` on the Windows Desktop for parity, though the logic is transport-only and passes on the Pi via the stubbed venv.
