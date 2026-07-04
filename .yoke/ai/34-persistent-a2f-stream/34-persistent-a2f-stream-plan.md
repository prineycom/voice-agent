# Epic 8 Phase 1: persistent a2f_stream via SDK Interactive executors — implementation plan

**Task:** https://github.com/prineycom/voice-agent/issues/34
**Complexity:** complex
**Mode:** sub-agents
**Parallel:** true

## Design decisions

### DD-1: Per-utterance boundary — keep the wire byte-compatible, change only semantics + lifecycle

**Decision:** Keep the exact `[u32 0]` stdout marker (`main.cpp:122-124`) but, after writing it, loop back to read the next utterance instead of exiting. EOF at the emotion-length read (`main.cpp:107`) becomes the clean shutdown signal. No byte on the wire changes.
**Rationale:** stdin already delimits an utterance with a trailing `[u32 0]` audio marker (`main.cpp:114` loops until `n==0`); the Python reader already breaks on `n==0` (`engine.py:128-129`). The only Python change is lifecycle (DD-3): stop closing stdin, stop `proc.wait()` per utterance.
**Alternative:** Add a new per-utterance handshake byte — rejected: churns both sides and the README/spike prose for no behavioral gain; the marker already exists.

### DD-2: Interactive executors and all one-time resources created once, before the loop

**Decision:** In `main()` after `SetCudaDeviceIfNeeded(0)` (`main.cpp:70`) and before the utterance loop, create once: the Interactive geometry executor (`nva2f::CreateRegressionGeometryInteractiveExecutor`, loads the bs1 engine from `modelJson()` once), the **Device** blendshape-solve interactive executor (`CreateDeviceBlendshapeSolveInteractiveExecutor`), the pinned host weight buffer (`main.cpp:82`), the results callback (`main.cpp:83`), and the CUDA stream. Emotion is set per utterance inside the loop.
**Rationale:** Device/GPU solve is mandatory — the CPU solver is ~150 s/utterance (spike `2026-07-03-a2f-3d-spike.md:214-216`). Creating once and tearing down at natural EOF avoids the per-utterance create/destroy and the double-free risk the investigator flagged for the CUDA stream + pinned buffer (`main.cpp:82,100`).
**Alternative:** Recreate executors per utterance — rejected: that is the ~3 s reload this task removes.

### DD-3: HelperBackend — module-singleton process + global asyncio.Lock + lazy-spawn + respawn-on-crash

**Decision:** `HelperBackend` holds one long-lived subprocess (`self._proc`) and an `asyncio.Lock`. `stream()` runs its critical section under `async with self._lock:`; `_ensure_proc()` spawns (the current `create_subprocess_exec` block, `engine.py:108-114`) only if `self._proc is None or self._proc.returncode is not None`. On `IncompleteReadError`/`BrokenPipeError` (`engine.py:136`): drain stderr, kill, set `self._proc = None`, raise `RuntimeError`. Remove `stdin.close()` (`engine.py:120`) and the per-utterance `proc.wait()` finally (`engine.py:139-140`).
**Rationale:** `backend = make_backend()` is a module-level singleton shared across all WS connections (`server.py:40`), so one shared process is naturally global; a shared stdin/stdout would interleave bytes across `stream()` calls without single-flight — the lock is required (investigator: no lock exists today). Lazy respawn keeps the service alive after a helper crash, which `server.py:99-101` surfaces but cannot itself recover.
**Alternative:** Per-connection process pool — rejected: multiplies VRAM/engine loads and defeats the load-once goal; serial single-flight is enough for the current traffic.

### DD-4: Collect frames under the lock, yield outside it

**Decision:** Read all frames of the utterance (until `[u32 0]`) into a buffer while holding the lock, release the lock, then yield. Preserve the 60→FPS stride (`engine.py:122`) and the exact yield shape `{"frame","t","arkit"}` (`engine.py:133`).
**Rationale:** The shared resource is the pipe; the lock must be held until the done marker or the next utterance corrupts the stream. Buffer is tiny (~28 frames × 68 f32 ≈ 7.6 KB, bounded by inference). Yielding outside the lock stops a slow WS consumer from stalling the shared pipe for other utterances.
**Alternative:** Yield frame-by-frame under the lock — rejected: couples the shared pipe to consumer speed.

### DD-5: Two-tier validation — GPU-free CI test (committed) + ephemeral box driver (scratch)

**Decision:** Commit a real pure-Python fake helper (`tests/fake_a2f_stream.py`) implementing the persistent stdin/stdout loop, and `tests/test_helper_backend.py` that drives the real `HelperBackend` against it (real spawn/lock/read/respawn — not mocked). The box 2-utterance / engine-loads-once / latency / VRAM check is an ephemeral scratch driver run in the Validation task, results captured in the spike doc — not committed.
**Rationale:** Closes the investigator's coverage gap (nothing exercises `HelperBackend` today) without needing a GPU in CI. `test_server.py` asserts only `ARKIT_52 ⊆ keys` so the 68-key output stays compatible.
**Alternative:** Only box validation — rejected: leaves the new lifecycle/locking untested in CI.

## Tasks

### Task 1: Port main.cpp to persistent Interactive executors (C++, on the box)

- **Files:** `infra/desktop/a2f/a2f_stream/main.cpp` (edit), `infra/desktop/a2f/a2f_stream/README.md` (edit)
- **Depends on:** none
- **Scope:** L
- **What:** Load the bs1 engine once and serve N utterances from one process, porting from the batch bundle to the Interactive geometry + Device blendshape-solve executors (DD-1, DD-2). Update the in-file protocol comment and the README.
- **How:** First (blocking) substep on the box: read `audio2face-sdk/include/audio2face/interactive_executor.h` + `interactive_executor_blendshapesolve.h` and factories at `audio2face-sdk/source/audio2face/audio2face.cpp:792-815` to nail the push/pop/flush/set-emotion API and whether the Device solve emits via the same `DeviceResults` callback (`main.cpp:59-65`). Then rewrite `main()`: create geometry + Device blendshape-solve interactive executors once from `modelJson()`, pinned buffer + callback + CUDA stream once (reuse `Destroyer`/`UniquePtr` `main.cpp:35-37`, framing I/O `main.cpp:44-54`). Loop: `while (readU32(emoLen))` → read+set emotion → `while(readU32(n)&&n)` push audio chunks + pop ready frames → per-utterance non-terminal flush for the ~0.5 s lookahead tail → pop remaining → `writeFrame`s → write `[u32 0]` done marker → loop; EOF at emotion read = shutdown. Update the protocol comment (`main.cpp:8-13`) — marker is per-utterance, process persists. Rewrite `README.md:15-30` to "persistent / working." Fallback if no non-terminal flush exists: flush via trailing-silence padding.
- **Context:** SDK/toolchain/GPU are box-only over SSH (`ssh Pavel@100.75.88.35` → `wsl -d Ubuntu -u root`). Stage `main.cpp` to `/root/a2f-sdk/a2f_stream/`; build with `build_helper.sh` (likely just a new `#include`, no script edit); keep the launching ssh session attached (WSL tears down otherwise); push scripts via `ssh … 'wsl … cp /dev/stdin /root/x.sh' < local`. bs1 model/engine at `_data/generated/audio2face-sdk/samples/data/james/`; libaudio2x.so at `_build/release/audio2x-sdk/lib/`. Read `main.cpp` (whole), `README.md:15-30`, spike `:209-220`, `arkit.py:33-43`.
- **Verify:** Compiles on the box; smoke-run one utterance from `sample-data/audio_1sec_16k_s16le.wav` → ~28 frames × 68 coeffs (parity with today). Full 2-utterance / engine-once check is Task 4.

### Task 2: Make HelperBackend persistent (Python)

- **Files:** `infra/desktop/a2f/engine.py` (edit)
- **Depends on:** none
- **Scope:** M
- **What:** Keep one persistent subprocess so per-utterance latency ≈ inference (DD-3, DD-4). Byte-compatible wire (DD-1) — codes against the agreed protocol, not Task 1's binary.
- **How:** Add `self._proc = None`, `self._lock = asyncio.Lock()`, `_ensure_proc()` (spawn from `engine.py:108-114` only if absent/exited, `A2F_MODEL_JSON` via env). In `stream()`: `async with self._lock:` → ensure proc → write `[emo][audio][0]` (`engine.py:116-118`, **no** `stdin.close()`) → read frames until `n==0` into a buffer applying the stride (`engine.py:122,131-135`) → on `IncompleteReadError`/broken pipe drain stderr, kill, `self._proc=None`, raise `RuntimeError` (`engine.py:136-138`) → release lock → yield buffered frames. Remove the `proc.wait()` finally (`engine.py:139-140`). Update the class docstring (`engine.py:73-85`) and module note (`engine.py:9-11`).
- **Context:** Preserve the exact yield shape (`engine.py:133`) `server.py:94-96` consumes; mirror `MockBackend` contract (`engine.py:42-70`). Read `engine.py:93-141`, `server.py:71-106`.
- **Verify:** `pytest tests/test_server.py` still green (mock path unaffected); read-through confirms the lock wraps the pipe critical section and stdin is never closed mid-life.

### Task 3: GPU-free persistent-protocol test

- **Files:** `infra/desktop/a2f/tests/fake_a2f_stream.py` (create), `infra/desktop/a2f/tests/test_helper_backend.py` (create)
- **Depends on:** Task 2
- **Scope:** M
- **What:** Exercise the real `HelperBackend` lifecycle (spawn/lock/read/respawn) without a GPU (DD-5).
- **How:** `fake_a2f_stream.py`: executable pure-Python; read `[emoLen][emo][audio…][0]` in a loop, emit deterministic frames + `[u32 0]`, stay alive; a magic emotion value → `sys.exit(1)` to simulate a crash. `test_helper_backend.py`: set `A2F_HELPER` to the fake + `A2F_BACKEND=helper`, `asyncio.run` two `stream()` calls; assert one spawn across both (count `create_subprocess_exec`), 60→30 stride correct, done-marker did not exit the proc between utterances, crash input → `RuntimeError` and the next call respawns.
- **Context:** Mirror the fixture style of `tests/test_server.py` (env set before import, `sys.path.insert`). Read `tests/test_server.py`, the final `engine.py` from Task 2.
- **Verify:** `pytest tests/` green locally, no GPU needed (soxr resample runs CPU-side).

### Task 4: Validation on the box + spike-doc update

- **Files:** `docs/research/2026-07-03-a2f-3d-spike.md` (edit)
- **Depends on:** all
- **Scope:** M
- **What:** End-to-end DoD proof and record (DD-5).
- **How:** On the box, run an ephemeral scratch driver feeding two utterances from `sample-data/audio_1sec_16k_s16le.wav` into the built `a2f_stream`; split stdout on `[u32 0]` markers → assert both ~28 frames and total wall ≈ one load (~3 s) + 2 inferences (not 2×3 s); confirm the engine-load log appears once; `nvidia-smi` steady-state VRAM (~0.3 GB). Then a WS smoke with `A2F_BACKEND=helper`: two utterances on one connection, per-utterance latency measured (≈ inference only). Run committed `pytest tests/` locally. Append results to the spike doc `:209-220` section.
- **Context:** Box-only over SSH (see Task 1 Context). Read the final `main.cpp`/`engine.py`, `docs/research/2026-07-03-a2f-3d-spike.md:209-220`.
- **Verify:** Engine loaded once for N utterances; both utterances complete frames; latency ≈ inference; VRAM steady; `pytest tests/` all green.

## Execution

- **Mode:** sub-agents
- **Parallel:** true
- **Reasoning:** Two disjoint lanes (box/C++ = Task 1, local Python = Task 2→Task 3) with zero file overlap; all remote box work is kept in the same lane because the WSL VM + GPU + attached ssh session are a serial shared resource.
- **Order:**
  Group 1 (parallel): Task 1, Task 2
  ─── barrier ───
  Group 2 (sequential): Task 3
  ─── barrier ───
  Group 3 (sequential): Task 4

## Verification

- `a2f_stream` loads the engine once and serves N utterances in one process with complete per-utterance frames.
- `HelperBackend` keeps one persistent process; per-utterance latency ≈ inference (no ~3 s reload).
- Verified on the box (2+ utterances, engine loaded once) and `docs/research/2026-07-03-a2f-3d-spike.md` updated.

## Materials

- Files: `infra/desktop/a2f/a2f_stream/{main.cpp,README.md}`, `infra/desktop/a2f/engine.py`, `infra/desktop/a2f/server.py`, `infra/desktop/a2f/arkit.py`, `infra/desktop/a2f/build_helper.sh`, `infra/desktop/a2f/tests/test_server.py`.
- Spike + findings: `docs/research/2026-07-03-a2f-3d-spike.md`.
- SDK: https://github.com/NVIDIA/Audio2Face-3D-SDK (headers under `audio2face-sdk/include/audio2face/`; factories `audio2face-sdk/source/audio2face/audio2face.cpp:792-815`).
- Environment: Desktop RTX 4070 (Windows) → Ubuntu WSL2 via `ssh Pavel@100.75.88.35` → `wsl -d Ubuntu -u root`. SDK checkout `/root/a2f-sdk/Audio2Face-3D-SDK`; helper staged at `/root/a2f-sdk/a2f_stream/`.
