# a2f_stream — C++ streaming helper (WIP)

The `helper` backend's inference core: reads PCM (+ emotion) on stdin, writes ARKit
blendshape frames on stdout (protocol at the top of `main.cpp`), running the
batch-1 TensorRT engine via the Audio2Face-3D-SDK (`libaudio2x`).

**Status:** compiles-shaped skeleton against the real SDK API (regression executor,
audio/emotion accumulators, results callback). Remaining before it's usable:
1. Extract ARKit coefficients from `IGeometryExecutor::Results` in the callback
   (the skin+tongue blendshape solve — confirm the accessor in
   `audio2face/executor_blendshapesolve.h` on the box).
2. Confirm `ReadRegressionGeometryExecutorBundle` signature (arg order/fps/stride).
3. Build + run against the engine from `../build_engine.sh`; validate frame parity
   vs the NIM output; measure VRAM.
4. Wire the subprocess bridge in `../engine.py` (`HelperBackend.stream`).

## Build

Inside the SDK checkout + TRT toolchain container (see `../build_engine.sh` header):
`../build_helper.sh` compiles `main.cpp` against the built `libaudio2x.so` and the
SDK headers, producing `a2f_stream`. Deploy it + `model.json` + `network.trt` to
`/opt/a2f/` and set `A2F_BACKEND=helper`.
