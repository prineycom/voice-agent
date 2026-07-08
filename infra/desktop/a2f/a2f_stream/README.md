# a2f_stream — C++ streaming helper

The `helper` backend's inference core: reads PCM (+ emotion tag) on stdin, writes ARKit
blendshape frames on stdout (protocol at the top of `main.cpp`), running the
batch-1 TensorRT engine via the Audio2Face-3D-SDK (`libaudio2x`). Since #40 it also runs
**Audio2Emotion (A2E)** on each utterance's audio as the baseline emotion source; the
stdin emotion tag is an additive boost via the SDK preferred-emotion channel.

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

- Created **once**, before the utterance loop: the geometry + Device blendshape-solve
  interactive executors, the pinned host weight buffer, the results callback, the CUDA
  stream — and (default, `A2E_ENABLED` != 0) the **A2E classifier interactive executor**
  from `A2E_MODEL_JSON` (default `/opt/a2f/a2e/model.json`). The A2E executor reads the
  same shared audio accumulator, writes post-processed emotion frames into the shared
  emotion accumulator via its results callback, and gets a dedicated preferred-emotion
  accumulator for the stdin tag.
- Per utterance: route the stdin emotion tag into the **preferred-emotion accumulator**
  (blending toggled per utterance, enabled only for a non-zero tag — lerping in a zero
  vector would suppress the A2E output) → push audio chunks into the shared audio
  accumulator → `Close()` it (the interactive executors compute frames only from a
  closed accumulator; `Close` also flushes the ~0.5 s centered-window lookahead tail) →
  run the **A2E pass** (`Invalidate` + `ComputeAllFrames`, batch-sequentially before the
  geometry pass) to fill the emotion accumulator with timestamped emotion frames, then
  close it → `Invalidate(kLayerAll)` + `ComputeAllFrames()` on the geometry/solve chain
  to emit every blendshape frame via the callback → write the `[u32 0]` done marker.
  Accumulators are `Reset()`/re-opened for the next utterance, so `Close` is a
  **per-utterance boundary, not terminal**
  (this is what the old batch `ReadRegressionBlendshapeSolveExecutorBundle` could not
  do: its `Close` was terminal and accumulator `Reset()` left the executor's read
  position stale, so utterance 2 yielded 0 frames).
- A failed/empty A2E pass degrades that utterance to tag-only (the emotion accumulator
  must not stay empty or the geometry pass emits no frames).
- **Rollback:** `A2E_ENABLED=0` — no A2E executor is created, the A2E model file is not
  required, and the tag is written straight into the emotion accumulator at t=0 (the
  pre-#40 tag-only behavior). The stdin/stdout protocol is byte-identical in both modes.
- EOF at the emotion-length read = clean shutdown.

## Env knobs

All optional; unset ⇒ the SDK/model-config defaults. Bad values warn and fall back —
never abort the helper. Production defaults are baked in `../deploy/Dockerfile`.

| env | what |
|---|---|
| `A2E_ENABLED` | `0` = one-flag rollback to tag-only (see Lifecycle). Anything else = A2E on. |
| `A2E_MODEL_JSON` | A2E model config path (default `/opt/a2f/a2e/model.json`). |
| `A2E_EMOTION_STRENGTH` | Final scale on the post-processed emotion vector — note it scales the tag boost too (SDK order: softmax → nullify neutral → keep-N → map to 10-dim → EMA blend → preferred lerp → smoothing → × strength). |
| `A2E_EMOTION_CONTRAST` | Softmax sharpening of the 6-class logits; applied *before* `neutral` is nullified, so sharpening a neutral-argmax utterance shrinks the mapped dims. |
| `A2E_LIVE_BLEND_COEF` / `A2E_LIVE_TRANSITION_TIME` | EMA blend / transition smoothing across emotion frames. |
| `A2E_MAX_EMOTIONS` | "Keep N largest" truncation. **Trap:** the network classifies only 6 emotions, so the model default (6) already truncates nothing; any value **> 6 hangs the helper** (unsigned underflow in the SDK CUDA post-process kernel spins the GPU forever) and **0 zeroes the output**. Leave unset. |
| `A2E_PREFERRED_STRENGTH` | Lerp weight of the preferred-emotion (tag) boost. Note the boost lerp suppresses non-tag A2E dims (a known, mechanistic property — see `docs/research/data/40-a2f-emotion-supply/analysis.md`). |
| `A2F_SKIN_STRENGTH` / `A2F_UPPER_FACE_STRENGTH` / `A2F_LOWER_FACE_STRENGTH` / `A2F_BLINK_STRENGTH` | Face-animator strengths, applied to the skin params at executor creation. |
| `A2F_BS_MULTIPLIERS` / `A2F_BS_OFFSETS` | Per-pose weight shaping CSVs, e.g. `browInnerUp=1.35,browDownLeft=1.25`. Pose names are the **solver's camelCase** names (`bs_skin.npz poseNames`), NOT the ARKit CamelCase the service emits — unknown names warn and no-op. Applied to the skin solver's creation-time config (the runtime setters are unreachable through the interactive executor). |

Production bake (tuned in #40): `A2F_BS_MULTIPLIERS="browInnerUp=1.35,browDownLeft=1.25,browDownRight=1.25"`,
everything else at model defaults.

## Footprint (with A2E, production image)

~1.65 GiB VRAM once both TRT engines are loaded on the first utterance (A2E ≈ 1.25 GiB of
it); first utterance ≈ 7.3 s end-to-end (engine load, under the baked
`A2F_HELPER_FIRST_TIMEOUT=60`), steady-state helper compute < 1.7 s for a 7.36 s
utterance. Ops details in `docs/research/data/40-a2f-emotion-supply/analysis.md`.

**SDK gotcha:** the geometry interactive executor must be created with
`ExecutionOption::All` — a `SkinTongue`-only geometry executor segfaults the Device
blendshape solve. The solve still emits exactly the 68 skin+tongue coefficients.

## Build

Inside the SDK checkout + TRT toolchain container (see `../build_engine.sh` header):
`../build_helper.sh` compiles `main.cpp` against the built `libaudio2x.so` and the
SDK headers, producing `a2f_stream`. Deploy it + `model.json` + `network.trt` to
`/opt/a2f/`, the A2E model dir (built by `../build_a2e_engine.sh`) to `/opt/a2f/a2e/`,
and set `A2F_BACKEND=helper`.
