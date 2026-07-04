// a2f_stream — streaming Audio2Face helper for the A2F WS service (`helper` backend).
//
// Reads an utterance on stdin, writes ARKit blendshape frames on stdout, running
// the batch-1 TensorRT engine via the Audio2Face-3D-SDK (libaudio2x). Uses the
// regression *blendshape-solve* executor bundle, whose executor emits solved
// blendshape weights (ARKit coefficients) — not raw geometry.
//
// Protocol (stdin/stdout, little-endian):
//   stdin :  [u32 emotionLen][emotionLen*f32 emotion]
//            then repeated [u32 nSamples][nSamples*f32 audio @16kHz mono]
//            then [u32 0]  == end-of-utterance
//   stdout:  first frame is prefixed by [u32 nCoeffs] once; then per frame
//            [u32 nCoeffs][nCoeffs*f32 weights]; then [u32 0] == done.
//
// Build: ../build_helper.sh (inside nvcr.io/nvidia/tensorrt:25.08-py3).

#include "audio2face/audio2face.h"
#include "audio2face/executor.h"
#include "audio2face/executor_blendshapesolve.h"
#include "audio2x/cuda_utils.h"
#include "audio2x/tensor_float.h"
#include "audio2x/io.h"

#include <cuda_runtime.h>

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <vector>

namespace {

struct Destroyer { template <typename T> void operator()(T* o) const { o->Destroy(); } };
template <typename T> using UniquePtr = std::unique_ptr<T, Destroyer>;
template <typename T> UniquePtr<T> ToUniquePtr(T* p) { return UniquePtr<T>(p); }

const char* modelJson() {
  const char* p = std::getenv("A2F_MODEL_JSON");
  return p ? p : "/opt/a2f/model.json";
}

bool readU32(std::uint32_t& out) { return std::fread(&out, sizeof(out), 1, stdin) == 1; }
std::vector<float> readFloats(std::uint32_t n) {
  std::vector<float> v(n);
  if (n) { if (std::fread(v.data(), sizeof(float), n, stdin) != n) v.clear(); }
  return v;
}
void writeFrame(const float* data, std::uint32_t n) {
  std::fwrite(&n, sizeof(n), 1, stdout);
  if (n) std::fwrite(data, sizeof(float), n, stdout);
  std::fflush(stdout);
}

// Per-frame blendshape-solve result → stdout. The GPU solver returns weights in
// device memory; copy to a pinned host buffer (passed as userdata), sync, emit.
// weights are the ARKit coefficients (skin + tongue solved).
bool onResults(void* ud, const nva2f::IBlendshapeExecutor::DeviceResults& r) {
  auto* host = static_cast<nva2x::IHostTensorFloat*>(ud);
  if (nva2x::CopyDeviceToHost(host->View(0, host->Size()), r.weights, r.cudaStream)) return false;
  cudaStreamSynchronize(r.cudaStream);
  writeFrame(host->Data(), static_cast<std::uint32_t>(host->Size()));
  return true;
}

}  // namespace

int main() {
  if (nva2x::SetCudaDeviceIfNeeded(0)) { std::cerr << "cuda init failed\n"; return 1; }

  // Single-track regression blendshape-solve bundle on the batch-1 engine.
  // GPU solver; 60 fps native (the service downsamples to A2F_FPS).
  auto bundle = ToUniquePtr(nva2f::ReadRegressionBlendshapeSolveExecutorBundle(
      /*nbTracks=*/1, modelJson(),
      nva2f::IGeometryExecutor::ExecutionOption::SkinTongue,
      /*useGpuSolver=*/true, /*frameRateNumerator=*/60, /*frameRateDenominator=*/1,
      /*outModelInfo=*/nullptr, /*outBlendshapeSolveModelInfo=*/nullptr));
  if (!bundle) { std::cerr << "failed to load bundle: " << modelJson() << "\n"; return 2; }

  // Pinned host buffer to receive each frame's device-side weights.
  auto hostWeights = ToUniquePtr(nva2x::CreateHostPinnedTensorFloat(bundle->GetExecutor().GetWeightCount()));
  if (bundle->GetExecutor().SetResultsCallback(onResults, hostWeights.get())) {
    std::cerr << "set callback failed\n"; return 3;
  }

  // One utterance per process invocation. Feed emotion + the full utterance's
  // audio, Close (TTS gives us the whole utterance at once, so Close flushes the
  // executor's ~0.5s lookahead → all frames), drain, emit a done marker, exit.
  //
  // NOTE: this batch executor can't be cleanly reused for a second utterance in
  // one process — Close is terminal, and the accumulators' Reset() does not reset
  // the executor's read position (verified: utterance 2 yields 0 frames), while a
  // no-Close continuous stream delays/mis-attributes frames by the lookahead. A
  // persistent (load-engine-once) service should use the SDK's *Interactive*
  // executors (CreateRegression...InteractiveExecutor / ...BlendshapeSolveInteractive),
  // which are built for streaming. Until then HelperBackend spawns per utterance.
  auto& audioAcc = bundle->GetAudioAccumulator(0);
  auto& emoAcc = bundle->GetEmotionAccumulator(0);
  auto stream = bundle->GetCudaStream().Data();
  auto drain = [&]() {
    while (nva2x::GetNbReadyTracks(bundle->GetExecutor()) > 0)
      bundle->GetExecutor().Execute(nullptr);
  };

  std::uint32_t emoLen = 0;
  if (!readU32(emoLen)) return 0;
  std::vector<float> emotion = readFloats(emoLen);
  emotion.resize(emoAcc.GetEmotionSize(), 0.0f);
  emoAcc.Accumulate(0, nva2x::HostTensorFloatConstView{emotion.data(), emotion.size()}, stream);
  emoAcc.Close();

  std::uint32_t n = 0;
  while (readU32(n) && n != 0) {
    std::vector<float> chunk = readFloats(n);
    audioAcc.Accumulate(nva2x::HostTensorFloatConstView{chunk.data(), chunk.size()}, stream);
    drain();
  }
  audioAcc.Close();
  drain();

  std::uint32_t done = 0;
  std::fwrite(&done, sizeof(done), 1, stdout);
  std::fflush(stdout);
  return 0;
}
