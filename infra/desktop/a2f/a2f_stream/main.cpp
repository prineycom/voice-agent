// a2f_stream — streaming Audio2Face helper for the A2F WS service (`helper` backend).
//
// Loads the bs1 TensorRT engine ONCE and serves N utterances from one long-lived
// process, running the Audio2Face-3D-SDK (libaudio2x) STREAMING executors: a
// regression geometry executor feeding a Device (GPU) blendshape-solve executor,
// which emits solved blendshape weights (ARKit coefficients).
//
// Protocol (stdin/stdout, little-endian) — one utterance per iteration:
//   stdin :  [u32 emotionLen][emotionLen*f32 emotion]
//            then repeated [u32 nSamples][nSamples*f32 audio @16kHz mono]
//            then [u32 0]          == end-of-utterance
//            or   [u32 0xFFFFFFFF] == abort utterance (no payload follows)
//   stdout:  per frame [u32 nCoeffs][nCoeffs*f32 weights]; then [u32 0] == done.
//   Frames are emitted WHILE audio chunks are still arriving: after every chunk
//   the helper runs every execution the accumulated audio allows and writes the
//   resulting frames immediately (the SDK's streaming low-latency pattern).
//   Closing the audio at end-of-utterance reveals the ~0.5 s centered-window
//   lookahead tail, and the remaining frames are drained before the done marker.
//   Abort (0xFFFFFFFF, valid ONLY at a chunk-header position): stop feeding and
//   computing, emit NO further frames for this utterance, write the NORMAL
//   [u32 0] done marker, then wait for the next utterance's emotion header
//   (whose per-utterance setup resets all accumulator/executor state).
//   The trailing [u32 0] is a PER-UTTERANCE boundary — the process stays alive
//   and the engine stays loaded, then loops back to read the next utterance's
//   emotion. EOF at the emotion-length read = clean shutdown.
//
// Emotion supply (A2E_ENABLED != "0", the default): an Audio2Emotion classifier
// streaming executor (env A2E_MODEL_JSON) infers prosody emotion from the same
// shared audio accumulator, interleaved with the geometry passes (geometry runs
// first; emotion inference runs only when geometry is blocked on it — the SDK's
// low-latency ordering), and fills the shared emotion accumulator with
// timestamped post-processed emotion frames; the stdin emotion tag is routed
// into the SDK preferred-emotion channel as a boost, enabled only when the tag
// is non-zero. With A2E_ENABLED=0 the tag is written straight into the emotion
// accumulator at t=0 and the accumulator is closed up-front (previous behavior;
// the A2E model file is then not required). The stdin/stdout protocol is
// byte-identical in both modes.
//
// Tuning knobs (optional envs; unset ⇒ the SDK/model-config defaults, i.e.
// exactly the previous behavior):
//   A2E post-process: A2E_EMOTION_STRENGTH, A2E_EMOTION_CONTRAST,
//     A2E_LIVE_BLEND_COEF, A2E_LIVE_TRANSITION_TIME, A2E_MAX_EMOTIONS,
//     A2E_PREFERRED_STRENGTH.
//   A2F face animator: A2F_SKIN_STRENGTH, A2F_UPPER_FACE_STRENGTH,
//     A2F_LOWER_FACE_STRENGTH, A2F_BLINK_STRENGTH.
//   Per-pose weight shaping: A2F_BS_MULTIPLIERS / A2F_BS_OFFSETS —
//     "BrowDownLeft=0.5,MouthSmileLeft=1.2" CSVs applied to the skin solver.
//
// Build: ../build_helper.sh (inside nvcr.io/nvidia/tensorrt:25.08-py3).

#include "env_knobs.h"

#include "audio2emotion/audio2emotion.h"
#include "audio2face/audio2face.h"
#include "audio2face/executor.h"
#include "audio2face/executor_blendshapesolve.h"
#include "audio2face/parse_helper.h"
#include "audio2x/audio_accumulator.h"
#include "audio2x/emotion_accumulator.h"
#include "audio2x/cuda_stream.h"
#include "audio2x/cuda_utils.h"
#include "audio2x/tensor_float.h"
#include "audio2x/io.h"

#include <cuda_runtime.h>

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace {

// Abort marker at a chunk-header position: not a length (a real chunk header is
// bounded by the parent's ~1 s audio chunks), so the value space is safe.
constexpr std::uint32_t kAbortMarker = 0xFFFFFFFFu;

struct Destroyer { template <typename T> void operator()(T* o) const { o->Destroy(); } };
template <typename T> using UniquePtr = std::unique_ptr<T, Destroyer>;
template <typename T> UniquePtr<T> ToUniquePtr(T* p) { return UniquePtr<T>(p); }

const char* modelJson() {
  const char* p = std::getenv("A2F_MODEL_JSON");
  return p ? p : "/opt/a2f/model.json";
}
const char* a2eModelJson() {
  const char* p = std::getenv("A2E_MODEL_JSON");
  return p ? p : "/opt/a2f/a2e/model.json";
}
// A2E_ENABLED=0 is the one-flag rollback path: tag-only emotion, no A2E
// executor created, A2E model file not required.
bool a2eEnabled() {
  const char* p = std::getenv("A2E_ENABLED");
  return !(p && std::strcmp(p, "0") == 0);
}

// envFloat / envInt / envCsvMap live in env_knobs.h (SDK-independent,
// unit-tested by test_env_knobs.cpp); only SDK-typed helpers stay here.

// Applies an A2F_BS_MULTIPLIERS / A2F_BS_OFFSETS CSV into the SKIN solver's
// creation-time config. Creation-time keeps the previous behavior: the shaping
// is baked in once and in effect from the very first frame, with no per-pose
// runtime mutation of the solver. Baseline is the model-config array when
// present, else the solver identity (multiplier 1, offset 0); unknown pose
// names warn and are skipped. `storage` backs the adjusted view and must
// outlive executor creation.
void applyBsEnvOverrides(const char* envName, bool isMultiplier,
                         nva2f::BlendshapeSolveExecutorCreationParameters::BlendshapeParams& skin,
                         std::vector<float>& storage) {
  const auto entries = envCsvMap(envName);
  if (entries.empty()) return;  // unset/empty/garbage-only env ⇒ config untouched
  const std::size_t n = skin.config.numBlendshapes;
  auto& view = isMultiplier ? skin.config.multipliers : skin.config.offsets;
  storage.assign(n, isMultiplier ? 1.0f : 0.0f);
  if (view.Data() && view.Size() == n)
    std::memcpy(storage.data(), view.Data(), n * sizeof(float));
  for (const auto& entry : entries) {
    std::size_t idx = n;
    for (std::size_t i = 0; i < skin.data.poseNamesSize && i < n; ++i) {
      if (skin.data.poseNames[i] && entry.first == skin.data.poseNames[i]) { idx = i; break; }
    }
    if (idx == n) {
      std::cerr << "a2f_stream: env " << envName << ": unknown pose '" << entry.first
                << "' — skipped\n";
      continue;
    }
    storage[idx] = entry.second;
  }
  view = nva2x::HostTensorFloatConstView{storage.data(), storage.size()};
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
// weights are the ARKit coefficients (skin + tongue solved). The device callback
// runs synchronously inside Execute(), so no frame can be emitted once the
// per-utterance loop stops calling Execute (the abort guarantee relies on this).
bool onResults(void* ud, const nva2f::IBlendshapeExecutor::DeviceResults& r) {
  auto* host = static_cast<nva2x::IHostTensorFloat*>(ud);
  if (nva2x::CopyDeviceToHost(host->View(0, host->Size()), r.weights, r.cudaStream)) {
    std::cerr << "a2f_stream: device-to-host weight copy failed — frame dropped\n";
    return false;
  }
  cudaStreamSynchronize(r.cudaStream);
  writeFrame(host->Data(), static_cast<std::uint32_t>(host->Size()));
  return true;
}

// Per-frame A2E post-processed emotion → the shared emotion accumulator the
// geometry executor reads (userdata). Emotions stay on-device; timestamps come
// from the A2E frame windows, in ascending order as Accumulate requires. The
// manual callback (rather than nva2e::CreateEmotionBinder) keeps the wiring
// explicit and identical to the previous interactive-executor build.
bool onEmotions(void* ud, const nva2e::IEmotionExecutor::Results& r) {
  auto* acc = static_cast<nva2x::IEmotionAccumulator*>(ud);
  return !acc->Accumulate(r.timeStampCurrentFrame, r.emotions, r.cudaStream);
}

}  // namespace

int main() {
  // Fail fast on a missing/unreadable A2E model: the ReadClassifierModelInfo
  // check below only fires AFTER the full A2F engine load — seconds of GPU
  // work wasted per respawn cycle when the path is wrong.
  if (a2eEnabled()) {
    std::FILE* f = std::fopen(a2eModelJson(), "rb");
    if (!f) { std::cerr << "failed to read a2e model info: " << a2eModelJson() << "\n"; return 2; }
    std::fclose(f);
  }

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
  // 1800 emotion frames per buffer ≈ 60 s at the A2E ~30 emotion-frames/s rate
  // (was 300 when the accumulator only ever held the single stdin tag frame).
  auto audioAcc = ToUniquePtr(nva2x::CreateAudioAccumulator(16000, 0));
  auto emoAcc = ToUniquePtr(nva2x::CreateEmotionAccumulator(emotionSize, 1800, 0));
  if (!audioAcc || !emoAcc) { std::cerr << "accumulator alloc failed\n"; return 2; }

  // Geometry streaming executor on the batch-1 engine (60 fps native; the
  // service downsamples to A2F_FPS). Full geometry (All): the Device
  // blendshape-solve executor requires the geometry executor to run all
  // outputs — a SkinTongue-only geometry executor crashes the device solve.
  // The extra jaw/eyes geometry doesn't change the solved skin+tongue weights.
  nva2f::GeometryExecutorCreationParameters geomParams;
  geomParams.cudaStream = stream;
  geomParams.nbTracks = 1;
  const nva2x::IAudioAccumulator* audioAccPtr = audioAcc.get();
  geomParams.sharedAudioAccumulators = &audioAccPtr;
  const nva2x::IEmotionAccumulator* emoAccPtr = emoAcc.get();
  geomParams.sharedEmotionAccumulators = &emoAccPtr;

  auto regressionParams = geomInfo->GetExecutorCreationParameters(
      nva2f::IGeometryExecutor::ExecutionOption::All, /*frameRateNumerator=*/60,
      /*frameRateDenominator=*/1);

  // Face knobs (A2F_*_STRENGTH) override the model-config skin animator params
  // at creation. Only applied when at least one env is set — otherwise the
  // model-info pointer passes through untouched. envFloat falls back to the
  // model-config field, so each unset env keeps its model value exactly.
  nva2f::IRegressionModel::GeometryExecutorCreationParameters::SkinParameters skinParams{};
  const bool skinEnvSet =
      std::getenv("A2F_SKIN_STRENGTH") || std::getenv("A2F_UPPER_FACE_STRENGTH") ||
      std::getenv("A2F_LOWER_FACE_STRENGTH") || std::getenv("A2F_BLINK_STRENGTH");
  if (skinEnvSet && regressionParams.initializationSkinParams) {
    skinParams = *regressionParams.initializationSkinParams;
    auto& sp = skinParams.params;
    sp.skinStrength = envFloat("A2F_SKIN_STRENGTH", sp.skinStrength);
    sp.upperFaceStrength = envFloat("A2F_UPPER_FACE_STRENGTH", sp.upperFaceStrength);
    sp.lowerFaceStrength = envFloat("A2F_LOWER_FACE_STRENGTH", sp.lowerFaceStrength);
    sp.blinkStrength = envFloat("A2F_BLINK_STRENGTH", sp.blinkStrength);
    regressionParams.initializationSkinParams = &skinParams;
  }

  auto geomExec = ToUniquePtr(nva2f::CreateRegressionGeometryExecutor(geomParams, regressionParams));
  if (!geomExec) { std::cerr << "failed to create geometry executor\n"; return 2; }

  // Device (GPU) blendshape-solve streaming executor. Device/GPU is mandatory:
  // the CPU solver is a ~184k×68 least-squares per frame (~150 s/utterance). It
  // takes ownership of the geometry executor (destroyed together at exit).
  const auto bsParams0 = bsInfo->GetExecutorCreationParameters(
      nva2f::IGeometryExecutor::ExecutionOption::All);
  nva2f::DeviceBlendshapeSolveExecutorCreationParameters bsParams;
  bsParams.initializationSkinParams = bsParams0.initializationSkinParams;
  bsParams.initializationTongueParams = bsParams0.initializationTongueParams;

  // Per-pose weight shaping (A2F_BS_MULTIPLIERS / A2F_BS_OFFSETS) rides in via
  // the skin solver's creation config — see applyBsEnvOverrides. The copied
  // params struct and the storage vectors stay alive for the executor's
  // lifetime; unset envs keep the model-info pointer untouched.
  nva2f::BlendshapeSolveExecutorCreationParameters::BlendshapeParams bsSkinParams{};
  std::vector<float> bsMultipliers, bsOffsets;
  if (bsParams0.initializationSkinParams &&
      (std::getenv("A2F_BS_MULTIPLIERS") || std::getenv("A2F_BS_OFFSETS"))) {
    bsSkinParams = *bsParams0.initializationSkinParams;
    applyBsEnvOverrides("A2F_BS_MULTIPLIERS", /*isMultiplier=*/true, bsSkinParams, bsMultipliers);
    applyBsEnvOverrides("A2F_BS_OFFSETS", /*isMultiplier=*/false, bsSkinParams, bsOffsets);
    bsParams.initializationSkinParams = &bsSkinParams;
  }

  auto bsExec = ToUniquePtr(nva2f::CreateDeviceBlendshapeSolveExecutor(
      geomExec.release(), bsParams));  // ownership of geomExec transferred here
  if (!bsExec) { std::cerr << "failed to create device blendshape solve executor\n"; return 3; }

  // Pinned host buffer + device-results callback — set ONCE.
  auto hostWeights = ToUniquePtr(nva2x::CreateHostPinnedTensorFloat(bsExec->GetWeightCount()));
  if (bsExec->SetResultsCallback(onResults, hostWeights.get())) {
    std::cerr << "set callback failed\n"; return 3;
  }

  // ---- optional A2E: prosody emotion inference + stdin tag as preferred boost ----
  // The classifier streaming executor IS the full A2E chain: TRT classifier
  // inference plus the emotion post-processor (smoothing, contrast, preferred-
  // emotion blending) run inside one executor, and its results callback emits
  // POST-PROCESSED emotion frames. The SDK's separate post-process executor is
  // NOT chained after it — that one zero-fills its own inference input (an
  // inference-free alternative source, not a downstream stage).
  UniquePtr<nva2e::IClassifierModel::IEmotionModelInfo> a2eInfo;  // owns memory the executor references
  UniquePtr<nva2x::IEmotionAccumulator> prefAcc;
  UniquePtr<nva2e::IEmotionExecutor> a2eExec;
  nva2e::PostProcessParams a2ePostParams{};
  if (a2eEnabled()) {
    a2eInfo = ToUniquePtr(nva2e::ReadClassifierModelInfo(a2eModelJson()));
    if (!a2eInfo) { std::cerr << "failed to read a2e model info: " << a2eModelJson() << "\n"; return 2; }

    // Mirror the SDK sample: 60000-sample window, 30 fps emotion output, 30
    // inferences skipped (one real inference per second, post-processed per frame).
    auto a2eModelParams = a2eInfo->GetExecutorCreationParameters(
        /*bufferLength=*/60000, /*frameRateNumerator=*/30, /*frameRateDenominator=*/1,
        /*inferencesToSkip=*/30);

    // A2E post-processed output must live in the exact emotion space the
    // geometry executor consumes — a size mismatch would corrupt the shared
    // accumulator (and the SDK would reject the preferred accumulator anyway).
    if (a2eModelParams.postProcessData.outputEmotionLength != emotionSize) {
      std::cerr << "a2e output emotion size " << a2eModelParams.postProcessData.outputEmotionLength
                << " != a2f emotion size " << emotionSize << "\n";
      return 2;
    }

    // Preferred-emotion channel: the stdin tag vector goes here per utterance;
    // blending is toggled per utterance and only enabled for a non-zero tag.
    prefAcc = ToUniquePtr(nva2x::CreateEmotionAccumulator(emotionSize, 4, 0));
    if (!prefAcc) { std::cerr << "preferred emotion accumulator alloc failed\n"; return 2; }
    const nva2x::IEmotionAccumulator* prefAccPtr = prefAcc.get();
    a2eModelParams.sharedPreferredEmotionAccumulators = &prefAccPtr;

    // A2E knobs override the model-config post-process baseline; envFloat
    // falls back to the config's current value, so each unset env keeps the
    // model default exactly. The result is the base params for BOTH executor
    // creation and the per-utterance enablePreferredEmotion toggle below —
    // A2E_PREFERRED_STRENGTH in particular lifts preferredEmotionStrength off
    // the model-config baseline it was previously stuck at.
    auto& pp = a2eModelParams.postProcessParams;
    pp.emotionStrength = envFloat("A2E_EMOTION_STRENGTH", pp.emotionStrength);
    pp.emotionContrast = envFloat("A2E_EMOTION_CONTRAST", pp.emotionContrast);
    pp.liveBlendCoef = envFloat("A2E_LIVE_BLEND_COEF", pp.liveBlendCoef);
    pp.liveTransitionTime = envFloat("A2E_LIVE_TRANSITION_TIME", pp.liveTransitionTime);
    pp.preferredEmotionStrength = envFloat("A2E_PREFERRED_STRENGTH", pp.preferredEmotionStrength);
    // The model-config maxEmotions IS the network's emotion class count; any
    // env value above it makes the SDK's CUDA post-process kernel spin forever
    // (unsigned underflow in its keep-N loop, verified live) — so bound the
    // override by the pre-override config value, not a hardcoded count.
    const long configMaxEmotions = static_cast<long>(pp.maxEmotions);
    const long maxEmotions = envInt("A2E_MAX_EMOTIONS", configMaxEmotions);
    if (maxEmotions < 0) {
      std::cerr << "a2f_stream: A2E_MAX_EMOTIONS must be >= 0 — keeping " << pp.maxEmotions << "\n";
    } else if (maxEmotions > configMaxEmotions) {
      std::cerr << "a2f_stream: A2E_MAX_EMOTIONS must be <= the model's emotion class count ("
                << configMaxEmotions << ") — keeping " << pp.maxEmotions << "\n";
    } else {
      pp.maxEmotions = static_cast<std::size_t>(maxEmotions);
    }
    a2ePostParams = a2eModelParams.postProcessParams;  // env-adjusted baseline for per-utterance toggling

    nva2e::EmotionExecutorCreationParameters a2eParams;
    a2eParams.cudaStream = stream;
    a2eParams.nbTracks = 1;
    a2eParams.sharedAudioAccumulators = &audioAccPtr;  // same audio the geometry reads

    a2eExec = ToUniquePtr(nva2e::CreateClassifierEmotionExecutor(a2eParams, a2eModelParams));
    if (!a2eExec) { std::cerr << "failed to create a2e classifier executor\n"; return 2; }
    if (a2eExec->SetResultsCallback(onEmotions, emoAcc.get())) {
      std::cerr << "set a2e callback failed\n"; return 3;
    }
  }

  // ---- per-utterance loop; the process and the loaded engine persist ----
  std::uint32_t emoLen = 0;
  while (readU32(emoLen)) {  // EOF here == clean shutdown
    std::vector<float> emotion = readFloats(emoLen);
    emotion.resize(emotionSize, 0.0f);
    const nva2x::HostTensorFloatConstView tagView{emotion.data(), emotion.size()};

    // Fresh accumulators for this utterance (Reset re-opens them), then rewind
    // the executors' consumer state (read positions, caches, HasExecutionStarted)
    // to sample 0 — the streaming-family equivalent of the old Invalidate() but
    // usable BEFORE the audio is complete. Also cleans up after an abort. The
    // executor resets MUST precede the per-utterance post-process param set
    // below: the SDK rejects SetExecutorPostProcessParameters once execution
    // has started on the track, and only Reset clears that state.
    emoAcc->Reset();
    audioAcc->Reset();
    if (bsExec->Reset(0)) { std::cerr << "a2f_stream: blendshape executor reset failed\n"; }
    if (a2eExec && a2eExec->Reset(0)) { std::cerr << "a2f_stream: a2e executor reset failed\n"; }

    if (a2eExec) {
      // Tag → preferred-emotion channel. The executor requires a provided
      // preferred accumulator to be CLOSED before computing, so it is filled
      // even for an all-zeros tag — but blending is only ENABLED for a
      // non-zero tag: blending in a zero vector would suppress the A2E output.
      bool tagNonZero = false;
      for (float v : emotion) if (v != 0.0f) { tagNonZero = true; break; }
      prefAcc->Reset();
      prefAcc->Accumulate(0, tagView, stream);
      prefAcc->Close();
      nva2e::PostProcessParams post = a2ePostParams;
      post.enablePreferredEmotion = tagNonZero;
      if (nva2e::SetExecutorPostProcessParameters(*a2eExec, 0, post)) {
        std::cerr << "a2f_stream: set a2e post-process params failed\n";
      }
      // emoAcc stays OPEN — the A2E results callback fills it incrementally as
      // audio streams in; it is closed once the audio is closed and A2E drained.
    } else {
      // Tag-only mode: one emotion frame at t=0 in a CLOSED accumulator means
      // the geometry executor sees a constant emotion for the whole utterance
      // and is gated on audio alone — with the streaming executors this yields
      // the same frames as the previous batch build, just emitted per chunk.
      emoAcc->Accumulate(0, tagView, stream);
      emoAcc->Close();
    }

    // Once an Execute returns an error, retrying every chunk would spin/log
    // forever, so the rest of the utterance degrades: a2eBroken ⇒ A2E treated
    // as drained (the close-time tag fallback still guarantees emotion data);
    // computeBroken ⇒ no more frames for this utterance (the done marker is
    // still emitted). These flags catch EXECUTE-level errors only — a failed
    // device-to-host copy inside onResults returns false to the executor,
    // which just skips that frame's emission (stderr-logged there).
    bool a2eBroken = false;
    bool computeBroken = false;

    // Transcribed from the SDK sample's RunExecutorStreamingLowLatency: prefer
    // geometry (it emits frames), run emotion only when geometry is blocked on
    // it, and close the emotion accumulator once the audio is closed and A2E
    // has nothing left — Close is what unblocks the final lookahead frames.
    auto processAvailableData = [&]() {
      if (computeBroken) return;
      while (true) {
        // Process available geometry+solve — every Execute emits frames via
        // onResults synchronously.
        if (nva2x::GetNbReadyTracks(*bsExec) > 0) {
          if (bsExec->Execute(nullptr)) {
            std::cerr << "a2f_stream: blendshape execute failed — "
                         "dropping remaining frames of this utterance\n";
            computeBroken = true;
            return;
          }
          continue;
        }
        // No geometry ready: run emotion, which may unblock further geometry.
        if (a2eExec && !a2eBroken && nva2x::GetNbReadyTracks(*a2eExec) > 0) {
          if (a2eExec->Execute(nullptr)) {
            std::cerr << "a2f_stream: a2e execute failed — falling back to tag-only emotion\n";
            a2eBroken = true;
          }
          continue;
        }
        if (audioAcc->IsClosed() && !emoAcc->IsClosed()) {
          // Audio is complete and A2E has no executions left ⇒ emotions are
          // complete. Under streaming, "A2E produced nothing" is only knowable
          // here — degrade to the tag at t=0 (previous tag-only behavior)
          // rather than closing an empty accumulator, which would starve the
          // geometry executor into emitting no frames at all.
          if (emoAcc->IsEmpty()) emoAcc->Accumulate(0, tagView, stream);
          emoAcc->Close();
          continue;  // closing may unblock the geometry tail
        }
        break;  // nothing ready and nothing to close — need more audio
      }
    };

    // Transcribed from the sample's dropUnusedData (single track): both
    // accumulators grow unbounded within an utterance otherwise. Emotion drops
    // are capped at LastAccumulatedTimestamp — the accumulator rejects dropping
    // past what it holds. Audio drops honor the LAGGING reader: geometry and
    // A2E share the audio accumulator, so take the min of their read cursors.
    auto dropUnusedData = [&]() {
      if (!emoAcc->IsEmpty()) {
        const auto timestampToRead = bsExec->GetNextEmotionTimestampToRead(0);
        const auto lastAccumulated = emoAcc->LastAccumulatedTimestamp();
        emoAcc->DropEmotionsBefore(std::min(timestampToRead, lastAccumulated));
      }
      const std::size_t sampleGeometry = bsExec->GetNextAudioSampleToRead(0);
      // A broken A2E no longer reads audio — letting its stalled cursor
      // constrain the drop would freeze memory trimming for the utterance.
      const std::size_t sample =
          (a2eExec && !a2eBroken)
              ? std::min(sampleGeometry, a2eExec->GetNextAudioSampleToRead(0))
              : sampleGeometry;
      audioAcc->DropSamplesBefore(sample);
    };

    // STREAMING core: accumulate each stdin chunk, then immediately compute and
    // emit every frame the audio-so-far allows — frames flow while the parent
    // is still sending audio. kAbortMarker at this header position cancels the
    // utterance: no further feeding or computing (Execute is what emits frames,
    // so stopping it stops emission), fall through to the done marker, and let
    // the next utterance's setup reset all state. EOF mid-utterance behaves as
    // end-of-utterance here and shuts down cleanly on the next header read.
    std::uint32_t n = 0;
    bool aborted = false;
    while (readU32(n) && n != 0) {
      if (n == kAbortMarker) { aborted = true; break; }
      std::vector<float> chunk = readFloats(n);
      audioAcc->Accumulate(nva2x::HostTensorFloatConstView{chunk.data(), chunk.size()}, stream);
      processAvailableData();
      dropUnusedData();
    }

    if (!aborted) {
      // End-of-utterance close reveals the ~0.5 s centered-window lookahead
      // tail; the final drain computes the remaining A2E frames, closes the
      // emotion accumulator (with the empty-A2E tag fallback) and emits the
      // trailing geometry frames.
      audioAcc->Close();
      processAvailableData();
    }

    // Per-utterance done marker — emitted on abort too (same wire format as a
    // completed utterance, just with fewer frames); does NOT exit the process.
    std::uint32_t done = 0;
    std::fwrite(&done, sizeof(done), 1, stdout);
    std::fflush(stdout);
  }
  return 0;
}
