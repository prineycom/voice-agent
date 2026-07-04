# a2f_stream — C++ streaming helper

The `helper` backend's inference core: reads PCM (+ emotion) on stdin, writes ARKit
blendshape frames on stdout (protocol at the top of `main.cpp`), running the
batch-1 TensorRT engine via the Audio2Face-3D-SDK (`libaudio2x`).

**Status: WORKING & PERSISTENT** (built + run on the box). Loads the bs1 engine
**once** and serves N utterances from one long-lived process, using the SDK's
**Interactive** executors: `CreateRegressionGeometryInteractiveExecutor` →
`CreateDeviceBlendshapeSolveInteractiveExecutor` (GPU solve, mandatory — the CPU
solver is ~150 s/utterance). A device-results callback copies the solved weights to
a pinned host buffer and writes them out. Verified on the box: **2 utterances in one
process → [28, 28] frames × 68 ARKit coefficients** (52 skin + 16 tongue), each with
max ≈ 0.31 / ~22 non-zero (parity with the old per-spawn batch path), ~1.0 s total
(engine loaded once, no per-utterance reload), clean exit. `HelperBackend` in
`../engine.py` drives it as a single long-lived process.

Build: `../build_helper.sh` (g++ against `libaudio2x.so`, run in the TRT container).

## Lifecycle

- The geometry + Device blendshape-solve interactive executors, the pinned host
  weight buffer, the results callback, and the CUDA stream are all created **once**,
  before the utterance loop.
- Per utterance: set the emotion vector → push audio chunks into the shared audio
  accumulator → `Close()` it (the interactive executor computes frames only from a
  closed accumulator; `Close` also flushes the ~0.5 s centered-window lookahead
  tail) → `Invalidate(kLayerAll)` + `ComputeAllFrames()` to emit every frame via the
  callback → write the `[u32 0]` done marker. The accumulator is `Reset()`/re-opened
  for the next utterance, so `Close` is a **per-utterance boundary, not terminal**
  (this is what the old batch `ReadRegressionBlendshapeSolveExecutorBundle` could not
  do: its `Close` was terminal and accumulator `Reset()` left the executor's read
  position stale, so utterance 2 yielded 0 frames).
- EOF at the emotion-length read = clean shutdown.

**SDK gotcha:** the geometry interactive executor must be created with
`ExecutionOption::All` — a `SkinTongue`-only geometry executor segfaults the Device
blendshape solve. The solve still emits exactly the 68 skin+tongue coefficients.

## Build

Inside the SDK checkout + TRT toolchain container (see `../build_engine.sh` header):
`../build_helper.sh` compiles `main.cpp` against the built `libaudio2x.so` and the
SDK headers, producing `a2f_stream`. Deploy it + `model.json` + `network.trt` to
`/opt/a2f/` and set `A2F_BACKEND=helper`.
