// a2f_stream — streaming Audio2Face helper for the A2F WS service (`helper` backend).
//
// Loads the bs1 TensorRT engine ONCE and serves N utterances from one long-lived
// process, running the Audio2Face-3D-SDK (libaudio2x) *Interactive* executors: a
// regression geometry interactive executor feeding a Device (GPU) blendshape-solve
// interactive executor, which emits solved blendshape weights (ARKit coefficients).
//
// Protocol (stdin/stdout, little-endian) — one utterance per iteration:
//   stdin :  [u32 emotionLen][emotionLen*f32 emotion]
//            then repeated [u32 nSamples][nSamples*f32 audio @16kHz mono]
//            then [u32 0]  == end-of-utterance
//   stdout:  per frame [u32 nCoeffs][nCoeffs*f32 weights]; then [u32 0] == done.
//   The trailing [u32 0] is a PER-UTTERANCE boundary — the process stays alive and
//   the engine stays loaded, then loops back to read the next utterance's emotion.
//   EOF at the emotion-length read = clean shutdown.
//
// The interactive executor computes frames only from a CLOSED audio accumulator,
// so a utterance's frames are emitted right after its end-of-utterance flush
// (the accumulator is Reset()/re-opened for the next utterance).
//
// Build: ../build_helper.sh (inside nvcr.io/nvidia/tensorrt:25.08-py3).

#include "audio2face/audio2face.h"
#include "audio2face/executor.h"
#include "audio2face/executor_blendshapesolve.h"
#include "audio2face/interactive_executor.h"
#include "audio2face/parse_helper.h"
#include "audio2x/audio_accumulator.h"
#include "audio2x/emotion_accumulator.h"
#include "audio2x/cuda_stream.h"
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

// A partial stdin read (fewer bytes than the wire protocol promised) means the
// parent writer was interrupted mid-message — the child cannot recover its place
// in the byte stream, so it aborts loudly rather than fabricating data.
[[noreturn]] void fatalTruncated(const char* what, std::size_t got, std::size_t want) {
  std::cerr << "a2f_stream: truncated stdin read (" << what << "): got " << got
            << " of " << want << " bytes — parent writer desync, aborting\n";
  std::exit(4);
}

// Reads a u32 length prefix. Returns false ONLY on a clean EOF exactly at a
// message boundary (0 bytes read) — the legitimate shutdown path. A partial read
// (1-3 bytes of the prefix) is a fatal stdin desync.
bool readU32(std::uint32_t& out) {
  const std::size_t got = std::fread(&out, 1, sizeof(out), stdin);
  if (got == sizeof(out)) return true;
  if (got == 0) return false;  // clean EOF at boundary — shutdown path
  fatalTruncated("u32 length prefix", got, sizeof(out));
}
std::vector<float> readFloats(std::uint32_t n) {
  std::vector<float> v(n);
  if (n) {
    const std::size_t got = std::fread(v.data(), sizeof(float), n, stdin);
    if (got != n)
      fatalTruncated("float payload", got * sizeof(float),
                     static_cast<std::size_t>(n) * sizeof(float));
  }
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

  // ---- one-time setup: load the bs1 engine ONCE, build persistent executors ----
  auto cudaStream = ToUniquePtr(nva2x::CreateCudaStream());
  if (!cudaStream) { std::cerr << "cuda stream failed\n"; return 2; }
  auto stream = cudaStream->Data();

  // Model info loads the network (bs1 TensorRT engine) + blendshape-solver data.
  auto geomInfo = ToUniquePtr(nva2f::ReadRegressionModelInfo(modelJson()));
  if (!geomInfo) { std::cerr << "failed to read model info: " << modelJson() << "\n"; return 2; }
  auto bsInfo = ToUniquePtr(nva2f::ReadRegressionBlendshapeSolveModelInfo(modelJson()));
  if (!bsInfo) { std::cerr << "failed to read blendshape solve model info\n"; return 2; }

  const std::size_t emotionSize = geomInfo->GetNetworkInfo().GetEmotionsCount();

  // Shared accumulators — persist across utterances (Reset() per utterance).
  auto audioAcc = ToUniquePtr(nva2x::CreateAudioAccumulator(16000, 0));
  auto emoAcc = ToUniquePtr(nva2x::CreateEmotionAccumulator(emotionSize, 300, 0));
  if (!audioAcc || !emoAcc) { std::cerr << "accumulator alloc failed\n"; return 2; }

  // Geometry interactive executor on the batch-1 engine (60 fps native; the
  // service downsamples to A2F_FPS). Full geometry (All): the Device
  // blendshape-solve interactive executor requires the geometry executor to run
  // all outputs — a SkinTongue-only geometry executor crashes the device solve.
  // The extra jaw/eyes geometry doesn't change the solved skin+tongue weights.
  nva2f::GeometryExecutorCreationParameters geomParams;
  geomParams.cudaStream = stream;
  geomParams.nbTracks = 1;
  const nva2x::IAudioAccumulator* audioAccPtr = audioAcc.get();
  geomParams.sharedAudioAccumulators = &audioAccPtr;
  const nva2x::IEmotionAccumulator* emoAccPtr = emoAcc.get();
  geomParams.sharedEmotionAccumulators = &emoAccPtr;

  const auto regressionParams = geomInfo->GetExecutorCreationParameters(
      nva2f::IGeometryExecutor::ExecutionOption::All, /*frameRateNumerator=*/60,
      /*frameRateDenominator=*/1);

  auto geomExec = ToUniquePtr(nva2f::CreateRegressionGeometryInteractiveExecutor(
      geomParams, regressionParams, /*batchSize=*/1));
  if (!geomExec) { std::cerr << "failed to create geometry interactive executor\n"; return 2; }

  // Device (GPU) blendshape-solve interactive executor. Device/GPU is mandatory:
  // the CPU solver is a ~184k×68 least-squares per frame (~150 s/utterance). It
  // takes ownership of the geometry executor (destroyed together at exit).
  const auto bsParams0 = bsInfo->GetExecutorCreationParameters(
      nva2f::IGeometryExecutor::ExecutionOption::All);
  nva2f::DeviceBlendshapeSolveExecutorCreationParameters bsParams;
  bsParams.initializationSkinParams = bsParams0.initializationSkinParams;
  bsParams.initializationTongueParams = bsParams0.initializationTongueParams;

  auto bsExec = ToUniquePtr(nva2f::CreateDeviceBlendshapeSolveInteractiveExecutor(
      geomExec.release(), bsParams));  // ownership of geomExec transferred here
  if (!bsExec) { std::cerr << "failed to create device blendshape solve interactive executor\n"; return 3; }

  // Pinned host buffer + device-results callback — set ONCE.
  auto hostWeights = ToUniquePtr(nva2x::CreateHostPinnedTensorFloat(bsExec->GetWeightCount()));
  if (bsExec->SetResultsCallback(onResults, hostWeights.get())) {
    std::cerr << "set callback failed\n"; return 3;
  }

  auto& exec = *bsExec;
  using Layers = nva2f::IGeometryInteractiveExecutor;

  // ---- per-utterance loop; the process and the loaded engine persist ----
  std::uint32_t emoLen = 0;
  while (readU32(emoLen)) {  // EOF here == clean shutdown
    std::vector<float> emotion = readFloats(emoLen);
    emotion.resize(emotionSize, 0.0f);
    emoAcc->Reset();
    emoAcc->Accumulate(0, nva2x::HostTensorFloatConstView{emotion.data(), emotion.size()}, stream);
    emoAcc->Close();

    // Fresh audio stream for this utterance (Reset re-opens the accumulator).
    audioAcc->Reset();

    // Push each audio chunk incrementally into the shared accumulator.
    std::uint32_t n = 0;
    while (readU32(n) && n != 0) {
      std::vector<float> chunk = readFloats(n);
      audioAcc->Accumulate(nva2x::HostTensorFloatConstView{chunk.data(), chunk.size()}, stream);
    }

    // Per-utterance NON-terminal flush: the interactive executor only computes
    // frames from a CLOSED accumulator, and Close reveals the ~0.5 s
    // centered-window lookahead tail. The executor itself is never destroyed
    // and the accumulator is Reset() next iteration, so Close is a per-utterance
    // boundary, not terminal (unlike the old batch executor). Invalidate drops
    // all cached state so this utterance's frames are computed from scratch.
    audioAcc->Close();
    exec.Invalidate(Layers::kLayerAll);

    // Pop every frame for this utterance; ComputeAllFrames fires the results
    // callback (→ writeFrame) once per frame in ascending timestamp order.
    exec.ComputeAllFrames();

    // Per-utterance done marker — does NOT exit the process.
    std::uint32_t done = 0;
    std::fwrite(&done, sizeof(done), 1, stdout);
    std::fflush(stdout);
  }
  return 0;
}
