# a2f_stream — C++ streaming helper (WIP)

The `helper` backend's inference core: reads PCM (+ emotion) on stdin, writes ARKit
blendshape frames on stdout (protocol at the top of `main.cpp`), running the
batch-1 TensorRT engine via the Audio2Face-3D-SDK (`libaudio2x`).

**Status: WORKING** (built + run on the box). Uses `ReadRegressionBlendshapeSolveExecutorBundle`
(geometry → GPU blendshape solve) with a device-results callback that copies the
solved weights to a pinned host buffer and writes them out. Verified: a 0.46 s WAV
→ **28 frames × 68 ARKit coefficients** (52 skin + 16 tongue), clean exit, sensible
values (max ≈ 0.31, ~22 non-zero). `HelperBackend` in `../engine.py` drives it.

Build: `../build_helper.sh` (g++ against `libaudio2x.so`, run in the TRT container).

**Latency optimisation (open):** the helper currently loads the TensorRT engine on
every spawn (a few seconds). Make it **persistent** — load once, loop over
utterances (reset the audio accumulator between them) — so per-utterance latency is
just inference (~1.3 ms/frame). Then flip `HelperBackend` to reuse one process.

## Build

Inside the SDK checkout + TRT toolchain container (see `../build_engine.sh` header):
`../build_helper.sh` compiles `main.cpp` against the built `libaudio2x.so` and the
SDK headers, producing `a2f_stream`. Deploy it + `model.json` + `network.trt` to
`/opt/a2f/` and set `A2F_BACKEND=helper`.
