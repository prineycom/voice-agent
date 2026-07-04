# Report: 34-persistent-a2f-stream

**Plan:** `.yoke/ai/34-persistent-a2f-stream/34-persistent-a2f-stream-plan.md`
**Mode:** sub-agents
**Status:** ✅ complete

## Tasks

| #   | Task                                              | Status                | Commit    | Concerns                          |
| --- | ------------------------------------------------- | --------------------- | --------- | --------------------------------- |
| 1   | Port main.cpp to persistent Interactive executors | ⚠️ DONE_WITH_CONCERNS | `048f70b` | 2 forced SDK deviations — below   |
| 2   | Make HelperBackend persistent (Python)            | ✅ DONE               | `be5fc8d` | —                                 |
| 3   | GPU-free persistent-protocol test                 | ✅ DONE               | `45391ad` | surfaced latent kill() race — fixed |
| 4   | Validation on the box + spike-doc update          | ✅ DONE               | `005a714` | —                                 |

All three review passes returned ✅ Approved (Task 1, Task 2, Task 3, Task 4 each reviewed against the code, not the report).

## Post-implementation

| Step          | Status      | Commit    |
| ------------- | ----------- | --------- |
| Validate      | ✅ pass     | (Task 4)  |
| Hardening     | ✅ done     | `3c26d07` |
| Documentation | ➖ N/A (opt-in off; README+spike doc updated in-task) | — |
| Format        | ➖ N/A (no black/ruff config in project) | — |

## Concerns

### Task 1: Port main.cpp — two forced SDK-API deviations (both output-preserving, documented)

1. **No incremental per-chunk frame pop.** The Interactive executor computes frames only from a *closed* audio accumulator ("Audio accumulator is not closed" → segfault if you compute mid-stream). So audio is pushed incrementally, but frames are popped once per utterance right after a non-terminal `Close()` + `Invalidate(kLayerAll)` + `ComputeAllFrames()`; the accumulator is `Reset()` for the next utterance and the executor persists. Functionally identical for the current wire (engine.py sends one chunk per utterance).
2. **Geometry executor created with `ExecutionOption::All`, not `SkinTongue`.** A SkinTongue-only geometry executor segfaults the Device blendshape solve inside `ComputeAllFrames`. The solve still emits exactly the 68 skin+tongue coefficients (the `james` model has no jaw/eyes poses), so output parity is preserved; the extra geometry does not affect the solved weights.

Both are documented in the in-file comments and the README. Verified on the box: 2 utterances → [28, 28] frames × 68 coeffs, resolving the batch-executor "utterance 2 = 0 frames" blocker.

### Task 3: surfaced a latent robustness bug in engine.py (fixed)

Building the GPU-free test surfaced that `HelperBackend`'s crash path called `proc.kill()` which can raise `ProcessLookupError` if the child self-exited and was already reaped — masking the intended `RuntimeError` on the crash→respawn path (the exact path the persistence change relies on). Independently flagged by Task 2's and Task 3's reviewers. Fixed in `3c26d07` (guard `proc.kill()` with `try/except ProcessLookupError`). Suite stayed green (6 passed) after the fix.

## Validation

- On-box 2-utterance / one-process run (RTX 4070, WSL2, TRT 25.08 container): UTT1 = 28 frames × 68, UTT2 = 28 frames × 68, same process, exit 0.
- Engine load ~1461 ms once; per-utterance latency ~81 ms (full wall time) vs the old ~3 s per-utterance reload — ~37× lower. Engine-loads-once shown behaviourally (single process serves both; UTT2's 81 ms rules out a reload; TRT emits no load log line).
- Steady-state VRAM Δ ≈ 403 MiB (~0.39 GB) for the bs1 engine; STT/TTS NSSM services (ports 8001/8002) stayed listening throughout.
- `cd infra/desktop/a2f && python -m pytest tests/` ✅ 6 passed (test_helper_backend ×4 + test_server ×2), run in a venv from `requirements.txt` (system interpreter lacks soxr/fastapi).
- No project lint / type-check / build tooling configured for this service → N/A.

## Changes summary

| File                                                     | Action   | Description                                                                 |
| -------------------------------------------------------- | -------- | -------------------------------------------------------------------------- |
| infra/desktop/a2f/a2f_stream/main.cpp                    | modified | Batch bundle → persistent Interactive geometry + Device blendshape-solve; engine loaded once, per-utterance loop, `[u32 0]` is a per-utterance boundary |
| infra/desktop/a2f/a2f_stream/README.md                   | modified | Documented the persistent lifecycle + the two SDK gotchas                   |
| infra/desktop/a2f/engine.py                              | modified | HelperBackend keeps one persistent subprocess: lazy-spawn, `asyncio.Lock` single-flight, respawn-on-crash, `ProcessLookupError`-guarded kill |
| infra/desktop/a2f/tests/fake_a2f_stream.py               | created  | Pure-Python fake helper mirroring the exact stdin/stdout wire protocol       |
| infra/desktop/a2f/tests/test_helper_backend.py           | created  | GPU-free tests: one spawn across 2 utterances, done-marker≠exit, stride, crash→respawn |
| docs/research/2026-07-03-a2f-3d-spike.md                 | modified | Recorded the persistent-executor validation results                         |

## Commits

- `9e620b0` #34 docs(34-persistent-a2f-stream): add implementation plan
- `be5fc8d` #34 feat(34-persistent-a2f-stream): make HelperBackend keep one persistent process
- `048f70b` #34 feat(34-persistent-a2f-stream): port a2f_stream to persistent Interactive executors
- `45391ad` #34 test(34-persistent-a2f-stream): add GPU-free persistent-protocol test for HelperBackend
- `3c26d07` #34 fix(34-persistent-a2f-stream): guard proc.kill() against ProcessLookupError on crash respawn
- `005a714` #34 docs(34-persistent-a2f-stream): record persistent a2f_stream validation results
