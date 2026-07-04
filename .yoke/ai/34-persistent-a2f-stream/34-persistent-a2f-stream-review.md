# Code Review: 34-persistent-a2f-stream

## Summary

### Context and goal

Turns the `a2f_stream` C++ helper from a per-utterance batch process into a persistent one — the TensorRT bs1 engine loads once and a single long-lived process serves an unbounded stream of utterances via the SDK's *Interactive* executors — and reworks `HelperBackend` to match: lazy-spawn one subprocess, guard the shared stdin/stdout pipe with an `asyncio.Lock` single-flight section, and respawn on crash. Validated on the box: ~1.5 s one-time load, ~81 ms/utterance thereafter (~37× over the old per-utterance reload).

### Key code areas for review

1. **`infra/desktop/a2f/engine.py:148-210` (`HelperBackend._utterance_io` / `stream`)** — the locked write+read critical section, timeout budget, and the kill-on-any-failure/respawn logic. Highest-risk area (cross-utterance shared state).
2. **`infra/desktop/a2f/engine.py:136-146` (`_terminate`) + `close()`** — bounded force-kill/reap and the graceful shutdown path.
3. **`infra/desktop/a2f/server.py` (`lifespan`)** — wires backend shutdown so the persistent child is not orphaned.
4. **`infra/desktop/a2f/a2f_stream/main.cpp:55-83` (read helpers) + per-utterance loop** — persistent Interactive-executor lifecycle and stdin desync detection.
5. **`infra/desktop/a2f/tests/{fake_a2f_stream.py,test_helper_backend.py}`** — GPU-free protocol harness and lifecycle assertions.

### Complex decisions

1. **Interactive executor as "streaming within an utterance, boundary (not terminal) across utterances"** (`main.cpp`) — per-utterance `Reset()`+`Close()`+`Invalidate(kLayerAll)`+`ComputeAllFrames`; documented + validated with 2 utterances on one process.
2. **Single `asyncio.Lock` with buffer-then-yield** (`engine.py`) — frames are read under the lock but yielded after release, so a slow WS consumer can't stall the shared pipe.
3. **Kill + clear `_proc` on ANY failure exit** (`engine.py`) — cancellation/timeout/pipe-error all force a clean respawn rather than reusing a desynced process; `CancelledError` is re-raised, not swallowed.

### Questions for the reviewer

1. Timeout constants: `A2F_HELPER_TIMEOUT=5s` (per-utterance) and `A2F_HELPER_FIRST_TIMEOUT=30s` (spawn + one-time load) — appropriate for the target hardware, or should the first-call budget be tighter/looser?
2. Is there a supervisor/watchdog above the WS layer, or is the per-await timeout now the only guard against a wedged helper?

### Risks and impact

- Persistence introduces cross-utterance shared state; the pre-existing self-healing (fresh process per call) is gone. The fixes close the two failure modes that mattered (cancellation desync, hung-helper wedge) and add orphan-process cleanup on shutdown.
- Real end-to-end behavior (engine-load-once, latency, VRAM) is proven on the box; cancellation/timeout paths are covered by unit tests, not by a live integration harness.

### Tests and manual checks

**Auto-tests:** `pytest tests/` → 7 passed (spawn-once across 2 utterances, done-marker≠exit, 60→30 downsample + ARKit keys, crash→respawn, and the new mid-utterance-interrupt-does-not-reuse-desynced-process). GPU-free.

**Manual scenarios:**
1. Two utterances on one WS connection → one process, ~81 ms/utterance (box-verified).
2. Truncated stdin to the helper → rc=4 + stderr diagnostic, no zero-filled frames (box-verified).
3. FastAPI shutdown → `backend.close()` closes the child cleanly (no orphan GPU process).

### Out of scope

- The two SDK-forced deviations (Close-as-boundary not incremental pop; geometry `ExecutionOption::All`) — justified and documented.
- `server.py:65` `model_loaded` health flag still hardcoded — natural follow-up, not addressed here.

## Commits

| Hash    | Description                                                                 |
| ------- | -------------------------------------------------------------------------- |
| 9e620b0 | #34 docs(34-persistent-a2f-stream): add implementation plan                |
| be5fc8d | #34 feat(34-persistent-a2f-stream): make HelperBackend keep one persistent process |
| 048f70b | #34 feat(34-persistent-a2f-stream): port a2f_stream to persistent Interactive executors |
| 45391ad | #34 test(34-persistent-a2f-stream): add GPU-free persistent-protocol test  |
| 3c26d07 | #34 fix(34-persistent-a2f-stream): guard proc.kill() against ProcessLookupError |
| 005a714 | #34 docs(34-persistent-a2f-stream): record persistent a2f_stream validation results |
| 3f00f97 | #34 docs(34-persistent-a2f-stream): add execution report                   |
| 514a7cf | #34 fix(34-persistent-a2f-stream): fix 4 review issues                     |

## Changed Files

| File                                            | +/-        | Description                                                     |
| ----------------------------------------------- | ---------- | -------------------------------------------------------------- |
| infra/desktop/a2f/engine.py                     | +202/-... | Persistent HelperBackend: lock, timeouts, kill-on-any-failure, `close()` |
| infra/desktop/a2f/a2f_stream/main.cpp           | +190/-... | Persistent Interactive executors + stdin desync detection      |
| infra/desktop/a2f/tests/test_helper_backend.py  | +199      | GPU-free lifecycle tests incl. cancellation regression         |
| infra/desktop/a2f/tests/fake_a2f_stream.py      | +103      | Protocol-accurate fake helper                                  |
| infra/desktop/a2f/a2f_stream/README.md          | +52/-...  | Persistent lifecycle + SDK gotchas                             |
| docs/research/2026-07-03-a2f-3d-spike.md        | +57       | Validation results                                             |
| infra/desktop/a2f/server.py                     | +10/-...  | FastAPI `lifespan` shutdown → `backend.close()`                |

## Issues Found

| Severity  | Score | Category    | File:line              | Description                                                                 |
| --------- | ----- | ----------- | ---------------------- | --------------------------------------------------------------------------- |
| Critical  | 85    | bugs        | infra/desktop/a2f/engine.py:139-167 | `CancelledError`/timeout inside the locked section reused a desynced process, silently corrupting later utterances |
| Important | 60    | performance | infra/desktop/a2f/engine.py:143-165 | No timeout on the awaits → a hung helper permanently wedges the single-flight lock |
| Important | 55    | quality     | infra/desktop/a2f/server.py             | No shutdown hook → persistent child orphaned on ungraceful stop, holding VRAM |
| Minor     | 30    | bugs        | infra/desktop/a2f/a2f_stream/main.cpp:56-60 | `readFloats` silently zero-filled a partial read instead of detecting stdin desync |

## Fixed Issues

| Issue                                      | Commit    | Description                                                                 |
| ------------------------------------------ | --------- | -------------------------------------------------------------------------- |
| CancelledError desync (engine.py)          | `514a7cf` | Kill + clear `_proc` on ANY failure exit; re-raise `CancelledError`; extracted `_utterance_io`/`_terminate` |
| No I/O timeout wedge (engine.py)           | `514a7cf` | `asyncio.wait_for` on the locked I/O; env-tunable `A2F_HELPER_TIMEOUT` (5s) / `A2F_HELPER_FIRST_TIMEOUT` (30s); timeout → crash path |
| Orphan process on shutdown (engine/server) | `514a7cf` | `HelperBackend.close()` (+ no-op `MockBackend.close()`) wired to a FastAPI `lifespan` shutdown |
| Partial-read zero-fill (main.cpp)          | `514a7cf` | Byte-granular reads distinguish clean EOF from mid-message truncation → stderr diagnostic + exit 4; box-verified (rc=4, no zero frames) |

## Skipped Issues

**All found issues were fixed.**

## Recommendations

- Consider a lightweight linter/formatter config (ruff) for `infra/desktop/a2f/` — none exists today, so style is enforced only by review.
- Follow-up: make `server.py:65` `model_loaded` report the real engine-load state now that the helper holds a loaded engine persistently.
- The `A2F_HELPER_*` timeout defaults are conservative guesses; tune against production hardware and consider a service-level watchdog above the WS layer.
