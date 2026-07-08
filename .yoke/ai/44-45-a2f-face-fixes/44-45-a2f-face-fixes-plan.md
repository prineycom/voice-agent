# A2F face delivery fixes: #44 bounded lossy frames + #45 streaming helper compute — implementation plan

**Task:** https://github.com/prineycom/voice-agent/issues/44 + https://github.com/prineycom/voice-agent/issues/45
**Complexity:** complex
**Mode:** sub-agents
**Parallel:** true

Commit convention for this combined effort: Task 1 commits as `#44 fix(44-a2f-frame-size): …`; Tasks 2-5 commit as `#45 feat(45-a2f-streaming): …`; ops tasks with repo changes reference their issue accordingly.

Diagnosis (this session, 2026-07-08): (a) 68-key ~2.1KB JSON frames on the lossy LiveKit channel are silently lost on WireGuard/Tailscale paths (MTU 1280; unreliable SCTP; LiveKit SDK does no size validation and tts_plugin swallows publish errors); (b) the A2F chain batches per utterance at four points (fork recv-after-send; server.py buffers all PCM; engine.py write-all-then-read; helper computes only on a CLOSED accumulator — interactive executor family limitation), so the face trails the voice by ~1.3s, the reply tail is cut by the 500ms stop-grace, and short replies pantomime after audio.

Verified SDK facts baked in: the streaming executor family (`CreateRegressionGeometryExecutor`, `CreateDeviceBlendshapeSolveExecutor`, `CreateClassifierEmotionExecutor`) takes the SAME creation-parameter structs the interactive code builds (#40 knob plumbing carries over); `SetExecutorPostProcessParameters(IEmotionExecutor&, trackIndex, params)` exists for the per-utterance preferred-emotion toggle; `IExecutor::Reset(trackIndex)` exists; the low-latency streaming pattern (`processAvailableData` + `DropEmotionsBefore`/`DropSamplesBefore`) is in the SDK sample `sample-a2f-a2e-executor/main.cpp:360-560`; frontend missing-key semantics = 0 (arkit-map.js:13, avatar.js:146 whole-map replace) — NO frontend change needed.

## Design decisions

### DD-1: #44 compaction mechanics
**Decision:** per frame: `rv = round(v, 3)`; drop keys where `rv == 0` (subsumes the |v|≥0.001 threshold and matches frontend missing-key=0 semantics); round `t` to 3 decimals after adding `reply_offset_s`; non-numeric values raise inside the existing defensive `try` → frame dropped.
**Rationale:** one rule instead of two; matches the measured 602-1068B sparse sizes.
**Alternative:** separate threshold+round — marginally larger, no visual difference; rejected.

### DD-2: #44 hard cap = 1100 bytes, greedy highest-|v| fill
**Decision:** `FRAME_MAX_BYTES = 1100` on the full serialized payload. Sort kept keys by `(-abs(v), name)` (deterministic); compute base envelope cost; greedily add keys within budget; serialize with `json.dumps(..., separators=(",", ":"))` (compact separators shave ~15% for free).
**Rationale:** 1280 − IP/UDP/DTLS/SCTP overhead ≈ 1150-1180 usable; real frames (median 897B sparse) rarely hit the cap; worst theoretical 1638B gets its least-significant keys dropped.
**Tests pin:** 68-key synthetic frame ≤1100B with highest-|v| retention + name tiebreak; typical frame untouched except rounding; zero-rounding keys absent; compact separators; malformed-frame defensiveness unchanged. Amend the "forwarded untouched" contract comment (test_tts_plugin.py:541-543; its `{"jawOpen": 0.5}` assertion still passes verbatim).
**Alternative:** serialize-then-shrink loop — O(n²), same result; rejected.

### DD-3: #44 placement and blast radius
**Decision:** compaction entirely inside the existing `try` (tts_plugin.py:415-429); wire shape `{type, frame, t, arkit}` unchanged; zero frontend changes (verified semantics cited above).
**Alternative:** binary/delta encoding — scope creep; sparse JSON suffices; rejected.

### DD-4: #45 executor-family switch shape
**Decision:** replace the three `*Interactive*` executors with the streaming family (same creation structs — knobs carry over; existing `onResults`/`onEmotions` callback types already match the streaming family). Main loop: read emotion header → per-utterance setup (accumulator Resets, prefAcc fill+close from tag, `SetExecutorPostProcessParameters(*a2eExec, 0, post)` toggle, `exec.Reset(0)`, `a2eExec->Reset(0)`) → per audio chunk: `Accumulate` + `processAvailableData()` (low-latency variant: geometry-first `Execute` while `GetNbReadyTracks>0`, then emotion; close emoAcc once audio closed and executions exhausted; `Drop*` trimming per the sample's `dropUnusedData`) → on `[u32 0]`: `audioAcc->Close()`, drain to completion, emit done marker. A2E-empty→tag fallback moves to close-time (the only point where "A2E produced nothing" is knowable). stdout protocol unchanged — frames simply start during accumulation.
**Rationale:** interactive family computes only on closed accumulators (verified); streaming family is the SDK-sanctioned path with identical structs.
**Alternative:** close/reset interactive accumulators per chunk — O(n²) recompute, breaks smoothing continuity; rejected.

### DD-5: barge-in = protocol-level abort, not respawn
**Decision:** distinguished stdin terminator `[u32 0xFFFFFFFF]` at the chunk-header position = abort utterance: helper stops computing, emits the NORMAL `[u32 0]` done marker, loops to the next utterance. engine.py: on consumer cancel/aclose mid-utterance at a message boundary → send abort, bounded-drain stdout to done (≤2s), helper survives (no respawn); any pipe error/timeout/boundary-uncertainty → existing kill+respawn path.
**Rationale:** with streaming, barge-in interrupts mid-feed on nearly every interruption; ~1.5s reload per barge-in is unacceptable; abort costs ~15 lines each side; marker lands on a message boundary so the fatalTruncated desync guard (main.cpp:129-143) is untouched.
**Alternative:** accept respawn cost — multi-second dead-face window per barge-in; rejected.

### DD-6: timeout model
**Decision:** delete the whole-utterance `wait_for(HELPER_TIMEOUT)`. New: (a) first stdout read after (re)spawn bounded by `A2F_HELPER_FIRST_TIMEOUT` (30/60 — unchanged, covers engine load); (b) every subsequent read individually bounded by `A2F_HELPER_TIMEOUT` (same env, default 5 — new per-read/inactivity semantics; Dockerfile unchanged); (c) writer `drain()` bounded by 5s. Timeout → kill+respawn+RuntimeError as today; crash semantics unchanged.
**Alternative:** duration-scaled wall-clock budget — duration unknown up front under streaming; rejected.

### DD-7: streaming resample
**Decision:** `soxr.ResampleStream(24000, 16000, 1, dtype='float32')` per utterance in the writer task; `resample_chunk(x, last=False)` per chunk; flush with `last=True` before the end marker; skip zero-length outputs. If installed soxr lacks ResampleStream, pin soxr version in requirements in the same commit.
**Rationale:** preserves filter continuity across chunks — per-chunk independent resample would click at boundaries and diverge from baselines.

### DD-8: fork interleave shape
**Decision:** split `A2FFork._run` into sender coroutine (queue drain → send; sentinel → `{"end"}`) + receiver coroutine (existing recv/forward loop), `asyncio.gather` inside one `websockets.connect` context; single best-effort exception envelope; `_fire_done` in `finally`; public API/`close()` unchanged → TTS server.py untouched.
**Alternative:** one-loop `asyncio.wait` on queue+recv — more states, easy to leak a pending recv; rejected.

### DD-9: server/engine API shape
**Decision:** backends grow `stream_from(chunks: AsyncIterator[bytes], emotion) -> AsyncGenerator[frame]` as primary; `stream(pcm, emotion)` stays as a one-chunk wrapper (MockBackend + existing tests keep working). server.py `/a2f`: queue-backed chunk iterator fed by the receive loop; concurrent forward loop sends frames immediately; `{"end"}` closes the iterator; `{"done": true}` after completion; disconnect mid-feed → `aclose()` (→ DD-5 abort). WS protocol byte-identical (frames just arrive before `end`). Engine internals: concurrent writer task (resample→framed stdin) + eager reader task (per-read timeouts) feeding an internal queue the generator yields from — reader eagerness prevents the stdout-pipe deadlock with a slow WS consumer.
**Alternative:** stateful begin/feed/finish object API — more surface, harder single-flight reasoning; rejected.

### DD-10: frontend — no change; idleMs stays 250
**Decision:** schedule.js anchor self-corrects (lag shrinks to first-frame latency + lagMs=100); keep idleMs=250 (frames still arrive ahead of real time — TTS synthesizes and helper computes faster than playback — so the buffer stays occupied mid-reply). Task 11 explicitly watches for mid-reply teardowns; a bump ships as a follow-up only if observed.
**Alternative:** raise idleMs pre-emptively — weakens the dropped-done net for no observed failure; rejected.

### DD-11: determinism acceptance — criteria-level, not bit-level
**Decision:** probe A/B on the same sha-verified WAV: neutral/joyboost/angerboost vs `docs/research/data/40-a2f-emotion-supply/confirm-baked-*.json`; acceptance = #40 criteria (a)-(d) still pass at their thresholds (group level) + frame count ±2 + no NaN/out-of-range. Bit-identical NOT required (chunk-boundary A2E windows and resample tails legitimately perturb low-order values).

### DD-12: deploy ordering / wire compatibility (verified against code)
- Pi tts_plugin (#44): output-only, frontend tolerates missing keys → deploys independently. ✅
- a2f_fork (NSSM voice-agent-tts): new fork ↔ old server = frames still arrive post-end (receiver idles); old fork ↔ new server = early frames buffered client-side by websockets (max_size=None) → drains after end. Ships independently. ✅
- helper + engine.py + server.py MUST ship atomically — and DO (all three in one image; build_image.sh copies server/engine, Dockerfile bakes a2f_stream). Proven necessity: new helper + old engine DEADLOCKS (old engine writes all stdin before reading stdout; streaming helper fills the 64KB stdout pipe after ~230 frames → blocks writeFrame → stops reading stdin → engine drain() blocks → timeout/respawn loop).
- Safe order: (1) Pi worker restart (#44); (2) NSSM TTS restart (fork); (3) one image rebuild + container recreate (helper+engine+server). Production works after every step. Container recreate reuses exact `docker inspect` run flags (supervision preserved).

### DD-13: #40 semantics carried over + A2E readiness contingency
**Decision:** preferred-emotion accumulator still filled+closed up-front from the stdin tag; per-utterance resets via `IExecutor::Reset(0)` (Invalidate is interactive-only); knobs apply at creation as today. Contingency (pre-decided): if Task 8 probing shows the first geometry frame gated on A2E's 60000-sample window beyond the ≤0.5s budget, pre-seed `emoAcc` with the tag (or neutral) at t=0 while leaving it open so A2E frames land behind it (~5-line change, one bounded loop-back Task 8→Task 3).

## Tasks

### Task 1: #44 sparse/rounded/capped blendshape payload (Pi)

- **Files:** `infra/pi/agent/tts_plugin.py` (edit :409-431 area), `infra/pi/agent/tests/test_tts_plugin.py` (edit)
- **Depends on:** none
- **Scope:** M
- **What:** Implement DD-1/DD-2/DD-3: compact separators; round `t` and values to 3 decimals; drop keys rounding to 0; deterministic greedy 1100-byte cap keeping highest-|v| keys (name tiebreak); all inside the existing defensive `try`.
- **How:** module constant `FRAME_MAX_BYTES = 1100`; extract a unit-testable helper `_compact_arkit(...) -> bytes`; amend the contract comment at test :541-543; new tests via the conftest `blendshapes=` hook per DD-2's pin list; `test_malformed_blendshape_frame_never_breaks_audio` stays green.
- **Context:** `tts_plugin.py:380-460`, `tests/test_tts_plugin.py:480-640`, `tests/conftest.py`, `infra/pi/web/static/js/arkit-map.js:1-20`.
- **Verify:** `cd infra/pi/agent && .venv/bin/python -m pytest tests/test_tts_plugin.py -q` — green incl. new pins. Commit `#44 fix(44-a2f-frame-size): bound blendshape frames for the lossy channel`.

### Task 2: #45 fork send/recv interleave (Desktop TTS side, Python)

- **Files:** `infra/desktop/tts/a2f_fork.py` (edit :103-131), `infra/desktop/tts/tests/test_a2f_fork.py`, `infra/desktop/tts/tests/fake_a2f.py`
- **Depends on:** none
- **Scope:** M
- **What:** DD-8: sender + receiver coroutines under one connection, gather, best-effort envelope and single `_fire_done` preserved; public API untouched.
- **How:** extend `fake_a2f.py` with early-frame emission (frames after first PCM chunk, before end); new test pins `on_frame` fires before `end()`; all existing tests stay green.
- **Context:** `a2f_fork.py` (whole), `tests/test_a2f_fork.py`, `tests/fake_a2f.py`, `infra/desktop/tts/server.py:180-255` (caller contract, read-only).
- **Verify:** `cd infra/desktop/tts && python -m pytest tests/ -q`. Commit `#45 feat(45-a2f-streaming): interleave fork send/recv for early frame forwarding`.

### Task 3: #45 helper streaming executors + abort protocol (C++)

- **Files:** `infra/desktop/a2f/a2f_stream/main.cpp` (edit)
- **Depends on:** none
- **Scope:** L
- **What:** DD-4 + DD-5 + DD-13: streaming executor family; per-chunk Accumulate + low-latency processAvailableData (geometry-first) + Drop* trimming; per-utterance Reset(0); tag→preferred channel via SetExecutorPostProcessParameters; A2E-empty→tag fallback at close-time; `[u32 0xFFFFFFFF]` abort marker → discard utterance, emit normal done marker, next utterance; update protocol comment block (:8-29).
- **How:** transcribe the SDK sample's `RunExecutorStreamingLowLatency` loop (readable over SSH: `ssh Pavel@100.75.88.35 "wsl -d Ubuntu -- sed -n 360,560p /root/a2f-sdk/Audio2Face-3D-SDK/audio2face-sdk/source/samples/sample-a2f-a2e-executor/main.cpp"`); keep onResults/onEmotions as-is; keep env-knob code (same structs); abort is a valid u32 only at the chunk-header read (fatalTruncated semantics preserved).
- **Context:** `main.cpp` (whole), `build_helper.sh`, SDK sample + headers over SSH.
- **Verify:** review-level (compile is Task 7); `test_env_knobs` still passes on the Pi (untouched file). Commit `#45 feat(45-a2f-streaming): streaming executors + per-chunk compute + abort marker`.

### Task 4: #45 engine.py streaming I/O, timeouts, abort (+ fake + tests)

- **Files:** `infra/desktop/a2f/engine.py`, `infra/desktop/a2f/tests/fake_a2f_stream.py`, `infra/desktop/a2f/tests/test_helper_backend.py`
- **Depends on:** Task 3 (protocol as committed)
- **Scope:** L
- **What:** DD-6/DD-7/DD-9 engine side + DD-5 engine side: `stream_from(chunks, emotion)` primary API + `stream(pcm, emotion)` back-compat wrapper (MockBackend gets it via shared base); concurrent writer task (per-utterance soxr.ResampleStream, framed chunks, end marker) + eager reader task (first-read = FIRST_TIMEOUT after spawn, else per-read HELPER_TIMEOUT) feeding an internal queue; abort-on-cancel at message boundary + bounded drain ≤2s, helper survives; else kill+respawn as today; `close()` unchanged.
- **How:** fake_a2f_stream.py emits N frames per received audio chunk (pins the interleaved protocol), plus abort-marker handling and a stall sentinel for the inactivity-timeout test; keep the crash sentinel. Adapt existing lifecycle pins; new pins: frames yielded before chunk iterator exhausted; abort → same `_proc` reused next utterance; inactivity timeout → respawn.
- **Context:** `engine.py` (whole), `tests/fake_a2f_stream.py`, `tests/test_helper_backend.py`, Task 3's committed protocol comment.
- **Verify:** `cd infra/desktop/a2f && python -m pytest tests/test_helper_backend.py -q`. Commit `#45 feat(45-a2f-streaming): streaming engine io with per-read timeouts and abort`.

### Task 5: #45 server.py incremental feed (+ tests)

- **Files:** `infra/desktop/a2f/server.py` (edit :79-114), `infra/desktop/a2f/tests/test_server.py`
- **Depends on:** Task 4 (`stream_from` API)
- **Scope:** M
- **What:** DD-9 server side: queue-backed chunk iterator fed by the receive loop; concurrent forward loop sends frames immediately; `{"end"}` closes the iterator; `{"done": true}` after completion; disconnect mid-feed → `aclose()`; error paths and multi-utterance-per-connection loop preserved; WS protocol byte-identical.
- **How:** keep the handler shape recognizable; new test pin: frames can arrive before the client sends end (mock `stream_from` yielding as chunks arrive); existing tests stay green.
- **Context:** `server.py` (whole), `tests/test_server.py`, Task 4's engine API docstring.
- **Verify:** `cd infra/desktop/a2f && python -m pytest tests/ -q`. Commit `#45 feat(45-a2f-streaming): incremental ws feed in a2f server`.

### Task 6: Ops (Pi) — deploy #44 + wire-size probe

- **Files:** none (ops)
- **Depends on:** Task 1
- **Scope:** S
- **What:** Deploy to the Pi worker (`./deploy.sh --pi` or the narrowest restart of voice-agent-worker — read deploy.sh first); then the LiveKit DC probe from the Pi (token :8095, room test): trigger a reply, capture forwarded blendshape payloads.
- **Verify:** max payload ≤ 1100 B over ≥100 frames; `t` monotonic; worker active; browser harness spot-check still animates.

### Task 7: Ops (Desktop) — sync repo + compile helper

- **Files:** none (ops; Desktop-op slot 1)
- **Depends on:** Task 3 (pushed)
- **Scope:** S-M
- **What:** push main; pull `/mnt/e/voice-agent-repo` (renormalize if line-ending conflicts — .gitattributes landed in #40 review); compile main.cpp in the TRT container per build_helper.sh (systemd-run for long jobs); run test_env_knobs in-container.
- **Verify:** binary at the path build_image.sh expects; compile exit 0; iterate repo-side on signature errors (max 3 iterations, then report).

### Task 8: Ops (Desktop) — image rebuild + container recreate + first-frame probe

- **Files:** none (ops; Desktop-op slot 2)
- **Depends on:** Tasks 4, 5 (pushed), 7
- **Scope:** M
- **What:** re-pull repo; build_image.sh (detached via systemd-run); capture current `docker inspect` run flags and recreate identically; health + VRAM (≈ unchanged ~1.65 GiB); a2f_probe through the tunnel measuring time-from-first-PCM-chunk-to-first-frame and total frames; probe-driven mid-feed disconnect → next utterance works with NO respawn in logs (abort proof).
- **Verify:** health ok; first-frame ≤ ~0.5s from first chunk (if gated on the A2E window → apply DD-13 contingency: one bounded loop-back to Task 3 + recompile + rebuild); frames arrive progressively (timestamp spread, not one burst); frame count ≈ duration×30; abort test passes.

### Task 9: Ops (Desktop) — determinism A/B vs #40 baselines

- **Files:** none (ops; Desktop-op slot 3)
- **Depends on:** Task 8
- **Scope:** M
- **What:** DD-11: probe neutral/joyboost/angerboost on the sha-verified WAV; analyze vs `docs/research/data/40-a2f-emotion-supply/confirm-baked-*.json`.
- **Verify:** #40 criteria (a)-(d) pass at group thresholds; frame count ±2; no NaN/out-of-range; record deltas in the report.

### Task 10: Ops (Desktop) — TTS service restart + /tts end-to-end probe

- **Files:** none (ops; Desktop-op slot 4)
- **Depends on:** Tasks 2 (pushed), 8
- **Scope:** M
- **What:** deploy the fork change to NSSM voice-agent-tts (narrowest restart; read deploy.sh/redeploy.ps1 to confirm which checkout the service runs); /tts probe (ws://100.75.88.35:8002/tts): count bin/blendshapes/done with timings.
- **Verify:** on 3 runs incl. one short (<1.5s) utterance: `first_blendshape − first_bin ≤ ~0.5s`; blendshape t coverage ≈ audio duration (tail present); exactly one a2f_done per sentence after audio done.

### Task 11: Ops (Pi) — live-room + real-browser verification

- **Files:** none (ops)
- **Depends on:** Tasks 6, 10
- **Scope:** M
- **What:** DC probe: first-blendshape vs state→speaking (target ≲0.6s, was ~1.3s); frames spread over the reply (not one burst); short-reply no-pantomime; barge-in mid-reply → face stops within grace AND next reply's first frame still fast (no respawn penalty). Then xvfb+CDP browser harness: `window.__a2fStats` shows no mid-reply teardowns across a multi-sentence reply (DD-10 watch-item); payloads ≤ 1100 B.
- **Verify:** all four live checks pass; before/after timing table in the report.

### Task 12: Validation

- **Files:** —
- **Depends on:** all
- **Scope:** M
- **What:** (a) full test sweep: Pi agent suite, infra/desktop/tts tests, infra/desktop/a2f tests, frontend `node --test`; (b) service health sweep (Pi worker/web; Desktop TTS :8002, A2F :8003; container restart policy); (c) USER-FACING acceptance: user opens https://ai.priney.com on their remote device (Tailscale path) and confirms — face animates in sync WHILE the agent speaks, no pantomime after a short reply, barge-in stops the face promptly. This is the acceptance gate for #44 and the UX gate for #45.
- **Verify:** all suites green + user confirms the three behaviors → close #44 and #45.

## Execution

- **Mode:** sub-agents
- **Parallel:** true
- **Reasoning:** #44 (Pi Python) fully independent of the #45 C++/Python chain until final verification; T1/T2/T3 touch disjoint files; Desktop-op tasks strictly serial (one machine); Pi ops may overlap Desktop ops.
- **Order:**
  Group 1 (parallel): Task 1, Task 2, Task 3
  ─── barrier (per-dependency) ───
  Group 2 (parallel): Task 4 (after T3), Task 6 (after T1)
  Group 3 (parallel): Task 5 (after T4), Task 7 (after T3; Desktop slot opens)
  ─── barrier ───
  Group 4 (sequential): Task 8 → Task 9 → Task 10 → Task 11 → Task 12

## Verification

- #44: remote device over Tailscale shows facedebug frames (`__a2fStats.frames > 0`); every forwarded frame ≤ 1100 B; helper/agent/frontend tests green; wire shape unchanged.
- #45: time-to-first-frame ≤ ~0.5s from first PCM chunk (probe + live timeline); face animates DURING speech; no tail truncation; short replies don't pantomime; barge-in cheap (no respawn); #39/#40 probe criteria still pass (criteria-level determinism); supervision/VRAM envelope unchanged.

## Materials

- Issues: #44, #45 (diagnosis of 2026-07-08 in both bodies).
- `docs/research/data/40-a2f-emotion-supply/` — baselines + analysis.md criteria.
- SDK sample `sample-a2f-a2e-executor/main.cpp:360-560` (Desktop) — streaming pattern.
- Session probes: /tts probe, DC timeline probe, xvfb+CDP browser harness (scratchpad; Task 6/10/11 executors rebuild them as needed under tools/ or scratch).
- Memory notes: Desktop SSH long-running procs (systemd-run, CRLF), A2F helper in production, Voice agent deployment.

## Open questions

None — the three candidate unknowns are resolved or bounded in DDs 4, 5, 13.
