# Report: 36-phase3-agent-livekit-a2f

**Plan:** `.yoke/ai/36-phase3-agent-livekit-a2f/36-phase3-agent-livekit-a2f-plan.md`
**Mode:** sub-agents
**Status:** ✅ complete

Epic 8 Phase 3 — the live TTS→face loop is closed end-to-end: the Desktop A2F
fork now forwards ARKit blendshape frames on the `/tts` socket, the agent
demuxes them onto the `voiceagent` DataChannel with reply-relative `t`, the
parsed emotion is routed into each `/tts` request, and barge-in stops A2F and
decays the face. All four code tasks landed, each reviewed; the two parallel
lanes (Desktop + Agent) coupled only through a frozen wire contract.

## Tasks

| #  | Task | Status | Commit | Concerns |
| -- | ---- | ------ | ------ | -------- |
| D1 | Fork forwards frames + relabeled `a2f_done` marker | ✅ DONE | `4b63bb6` (+ fix `8e92f22`) | — |
| A1 | Plugin demux, re-base `t`, emotion, inject publisher, reply-`{done}` (+ tests) | ✅ DONE | `4d69b7c` | 3 Minor (non-blocking) |
| A2 | Entrypoint wiring (publisher + emotion source) | ✅ DONE | `1a9440f` | — |
| D2 | `/tts` single-writer queue + fork multiplex | ⚠️ DONE_WITH_CONCERNS | `db778be` | test_server.py edit; fork-hang follow-up |
| V  | Validation | ✅ DONE | — | Desktop suite manual; sync gate deferred to live e2e |

Execution order: **Group 1** (parallel) D1 ∥ A1 → barrier → **Group 2** (parallel) D2 ∥ A2 → barrier → **V**.

## Post-implementation

| Step | Status | Commit |
| ---- | ------ | ------ |
| Validate | ✅ pass | — |
| Documentation | ⏭️ skipped (no `--update-docs`) | — |
| Format | ➖ N/A (no formatter/linter configured in project) | — |

## Review loop

Every code task got a spec + quality review pass:

- **D1** — reviewer found 1 Important issue (the `on_frame` callback was not exception-guarded, violating "a raising callback must not break the fork"). Fixed in `8e92f22` (per-frame guard mirroring `on_done` + a test that raises on the first frame). Re-review criteria satisfied; 8/8 fork tests pass.
- **A1** — ✅ Approved. 3 Minor, non-blocking: (1) emotion also added to the one-shot `ChunkedStream` path (correct — the wire contract carries `emotion` on every request); (2) `_swallow_task` catches `Exception` not `BaseException` (negligible — only a cancelled detached publisher at loop shutdown; never reaches audio); (3) a hypothetical blocking sync publisher would stall recv (production `publish_data` never blocks). Recorded, not fixed.
- **A2** — ✅ Approved, no issues. `publish_motion` accepts raw `bytes` and publishes lossy on `UI_TOPIC` — injected directly.
- **D2** — ✅ Approved. 17 tests pass across 3 runs, zero flakiness. 1 Minor: see Concerns.

## Concerns

### D2: test_server.py edit outside the stated file list — justified
The new contract emits exactly one `{"type":"a2f_done"}` per sentence in **every**
configuration (including A2F disabled, via a handler fallback). The pre-existing
`test_ws_streams_multiple_messages_on_one_connection` reads sentence-1's buffered
`a2f_done` at the start of sentence 2 and fails. The fix relaxes only the frame
collector to tolerate `a2f_done`/`blendshapes` between sentences; it weakens no
audio/protocol assertion (PCM equality and the terminal `{"done":true}` break
condition are intact). This is a direct, unavoidable consequence of the frozen
contract, not a masked bug.

### D2/D1: fork connect-then-hang could stall a normal reply (Minor, follow-up)
Because the agent's normal-reply exit now hard-requires `seen_a2f_done >= total_sends`
(A1), a fork that **connects but then hangs forever** without sending `{"done"}`/
`{"error"}` would never fire `on_done`, so no `a2f_done` is emitted and the agent
would hang on that reply. This is pre-existing behavior in the out-of-scope
`a2f_fork.py` recv loop (`while True: await a2f.recv()`), does not reproduce with
the real A2F server (which always sends `done`), and does **not** affect the
barge-in path (cancel terminates `_recv` via task cancellation, independent of the
a2f count — confirmed in the A1 review). **Recommended follow-up:** wrap the fork's
blendshape recv in an `asyncio.wait_for` timeout as a hardening pass.

### A1: hard cross-task coupling (documented, satisfied)
The agent's normal exit depends on the Desktop emitting exactly one `a2f_done`
per sentence. D2 guarantees this in all three paths (fork-success, fork-fail-fast,
no-fork fallback), verified byte-for-byte in the D2 review and the contract grep.

## Validation

- Agent full suite (Pi, `infra/pi/agent`): `.venv/bin/python -m pytest tests/ -q` → **100 passed**, 1 pre-existing unrelated silero DeprecationWarning.
- Desktop fork tests (run on the Pi via ad-hoc venv — GPU module stubbed by conftest): `test_a2f_fork.py` → **8 passed**; `test_server_fork.py` + `test_server.py` → **green ×5 runs, no flakiness** (D2 executor) and **17 passed ×3** (D2 reviewer). NOTE: per project convention these normally run on the Windows Desktop; the logic is transport-only and GPU-independent, so a manual Desktop rerun is optional confirmation.
- Cross-service wire contract confirmed byte-identical: Desktop emits `{"type":"blendshapes",...}` (verbatim from A2F) and `{"type":"a2f_done"}`; the agent consumes exactly those, re-bases `t`, forwards `{"type":"blendshapes",...}` + one reply-level `{"done":true}` on `voiceagent` (`reliable=False`) — matching the #35-pinned frontend consumer.
- `py_compile` → OK on all 9 touched files.
- Lint / type-check / build → N/A (project configures none).

### Deferred to live e2e (measure-first gate, ADR-0013 — no code)
- Speak a multi-sentence, emotion-tagged reply on the real Desktop+agent+browser stack; confirm Hiyori's face tracks speech, one `voiceagent {done}` per reply, barge-in decays the face, and `?lipsync=volume` retreat works.
- Capture the `mouth.js` `crossCorrelateOffset` "mouth sync offset: N ms" log across several utterances; record the offset and decide: keep apply-on-arrival vs enable the adaptive delay loop vs retreat mouth opening to `VolumeLipSync` (threshold: lead ≤ ~45 ms, lag ≤ ~125 ms).

## Changes summary

| File | Action | Description |
| ---- | ------ | ----------- |
| infra/desktop/tts/a2f_fork.py | modified | Forward A2F frames via `on_frame`; suppress A2F `{done}`, emit `on_done()` once in `finally`; guard `on_frame` against raising consumers |
| infra/desktop/tts/tests/test_a2f_fork.py | modified | Assert verbatim frame forwarding, `on_done` exactly-once in all paths, raising-`on_frame` robustness |
| infra/desktop/tts/server.py | modified | Single-writer outbound queue on `/tts`; PCM/`done` reliable, blendshapes drop-on-full; per-sentence gated `a2f_done` (fork + no-fork fallback) |
| infra/desktop/tts/tests/test_server_fork.py | modified | Tolerate interleaved frames; assert forwarded blendshapes + exactly-one `a2f_done` (incl. A2F-down) |
| infra/desktop/tts/tests/test_server.py | modified | Relax frame collector to tolerate `a2f_done`/`blendshapes` between sentences |
| infra/pi/agent/tts_plugin.py | modified | Demux blendshapes/`a2f_done`/audio-`done`; re-base `t` reply-relative; route emotion per sentence; `set_publisher`/`set_emotion_source`; one reply-`{done}` on end + barge-in |
| infra/pi/agent/tests/conftest.py | modified | `FakeTTSServer` emits blendshape frames + `a2f_done`; publisher spy |
| infra/pi/agent/tests/test_tts_plugin.py | modified | Emotion threading, monotonic `t` re-basing, one reply-`{done}`, publish-failure isolation, A2F-absent isolation, barge-in |
| infra/pi/agent/agent.py | modified | Hoist `DesktopTTS`; inject `publish_motion` + `lambda: agent.current_emotion` after agent construction |

## Commits

- `68b2fe3` #36 docs(36-phase3-agent-livekit-a2f) add implementation plan
- `4b63bb6` #36 feat(36-phase3-agent-livekit-a2f): forward A2F blendshape frames from the TTS fork
- `8e92f22` #36 fix(36-phase3-agent-livekit-a2f): guard on_frame callback so a raising consumer cannot kill the A2F drain loop
- `4d69b7c` #36 feat(36-phase3-agent-livekit-a2f): demux A2F blendshapes and route emotion in the agent TTS plugin
- `1a9440f` #36 feat(36-phase3-agent-livekit-a2f): wire voiceagent publisher and emotion source into DesktopTTS
- `db778be` #36 feat(36-phase3-agent-livekit-a2f): multiplex A2F blendshapes onto /tts via a single-writer queue
