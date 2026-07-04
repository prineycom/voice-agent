// a2f_stream — streaming Audio2Face helper for the A2F WS service.
//
// STATUS: WIP skeleton. The service (../server.py) runs today on the `mock`
// backend; this helper is the `helper` backend's inference core. It compiles
// against the Audio2Face-3D-SDK (libaudio2x) and is structured around the real
// SDK API, but the geometry->ARKit blendshape extraction (marked TODO) still
// needs to be wired against the SDK headers and verified on the GPU box.
//
// Protocol (stdin/stdout, length-prefixed little-endian):
//   stdin :  [u32 emotionLen][emotionLen*f32 emotion]
//            then repeated [u32 nSamples][nSamples*f32 audio @16kHz]
//            then [u32 0]  == end-of-utterance
//   stdout:  repeated [u32 nCoeffs][nCoeffs*f32 arkit]   one message per frame
//            then [u32 0]  == done
//
// Build: see ../build_helper.sh (runs inside nvcr.io/nvidia/tensorrt:25.08-py3).

#include "audio2face/audio2face.h"
#include "audio2face/executor_regression.h"
#include "audio2face/executor_blendshapesolve.h"
#include "audio2face/emotion.h"
#include "audio2x/cuda_utils.h"

#include <cstdint>
#include <cstdio>
#include <iostream>
#include <memory>
#include <vector>

using nva2f::UniquePtr;

namespace {

// model.json for the batch-1 engine produced by ../build_engine.sh, copied next
// to the helper at deploy time (A2F_ENGINE dir).
const char* kModelJson = std::getenv("A2F_MODEL_JSON")
    ? std::getenv("A2F_MODEL_JSON")
    : "/opt/a2f/model.json";

bool readU32(std::uint32_t& out) {
  return std::fread(&out, sizeof(out), 1, stdin) == 1;
}
std::vector<float> readFloats(std::uint32_t n) {
  std::vector<float> v(n);
  if (n) std::fread(v.data(), sizeof(float), n, stdin);
  return v;
}
void writeFrame(const std::vector<float>& coeffs) {
  std::uint32_t n = static_cast<std::uint32_t>(coeffs.size());
  std::fwrite(&n, sizeof(n), 1, stdout);
  if (n) std::fwrite(coeffs.data(), sizeof(float), n, stdout);
  std::fflush(stdout);
}

}  // namespace

int main() {
  constexpr int deviceID = 0;
  if (nva2x::SetCudaDeviceIfNeeded(deviceID)) { std::cerr << "cuda init failed\n"; return 1; }

  // Single-track regression executor bundle on the batch-1 engine + skin/tongue
  // blendshape solve (60 fps native; the service downsamples to A2F_FPS).
  auto bundle = nva2f::ToUniquePtr(nva2f::ReadRegressionGeometryExecutorBundle(
      /*nbTracks=*/1, kModelJson,
      nva2f::IGeometryExecutor::ExecutionOption::SkinTongue,
      /*fps=*/60, /*stride=*/1, /*progress=*/nullptr));
  if (!bundle) { std::cerr << "failed to load model bundle: " << kModelJson << "\n"; return 2; }

  // Result callback: one Results per produced frame. TODO: extract the ARKit
  // blendshape coefficients from `results` here (the SDK routes geometry through
  // the skin/tongue blendshape solver — confirm the coefficient accessor in
  // audio2face/executor_blendshapesolve.h) and hand them to writeFrame().
  auto callback = [](void* /*ud*/, const nva2f::IGeometryExecutor::Results& results) -> bool {
    std::vector<float> arkit;  // TODO: fill from results (skin + tongue coeffs)
    (void)results;
    writeFrame(arkit);
    return true;
  };
  bundle->GetExecutor().SetResultsCallback(callback, nullptr);

  // Emotion (first message). Applied to track 0 for the whole utterance.
  std::uint32_t emoLen = 0;
  if (!readU32(emoLen)) return 0;
  std::vector<float> emotion = readFloats(emoLen);
  auto& emoAcc = bundle->GetEmotionAccumulator(0);
  emotion.resize(emoAcc.GetEmotionSize(), 0.0f);
  emoAcc.Accumulate(0, nva2x::HostTensorFloatConstView{emotion.data(), emotion.size()},
                    bundle->GetCudaStream().Data());
  emoAcc.Close();

  // Stream audio chunks -> accumulate -> execute -> callback emits frames.
  auto& audioAcc = bundle->GetAudioAccumulator(0);
  std::uint32_t n = 0;
  while (readU32(n) && n != 0) {
    std::vector<float> chunk = readFloats(n);
    audioAcc.Accumulate(nva2x::HostTensorFloatConstView{chunk.data(), chunk.size()},
                        bundle->GetCudaStream().Data());
    while (nva2x::GetNbReadyTracks(bundle->GetExecutor()) > 0)
      bundle->GetExecutor().Execute(nullptr);
  }
  audioAcc.Close();
  while (nva2x::GetNbReadyTracks(bundle->GetExecutor()) > 0)
    bundle->GetExecutor().Execute(nullptr);

  std::uint32_t done = 0;
  std::fwrite(&done, sizeof(done), 1, stdout);
  std::fflush(stdout);
  return 0;
}
