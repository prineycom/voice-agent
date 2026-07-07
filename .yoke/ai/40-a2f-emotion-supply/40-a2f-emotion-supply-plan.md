# Emotion supply: A2E audio inference in the A2F helper + emotion-tag boost + SDK tuning (ADR-0016) — implementation plan

**Task:** https://github.com/prineycom/voice-agent/issues/40
**Complexity:** complex
**Mode:** sub-agents
**Parallel:** true

Gate resolved before planning: #39 calibration (`docs/research/2026-07-07-a2f-emotion-calibration.md`) confirmed forced emotion vectors move the face strongly (brows 9.44×/6.70×, emotion-specific patterns) → this issue proceeds as written; A2E integration is PRIMARY scope, SDK tuning secondary. Carry-overs: BrowDownLeft≈0.444 anomaly under joy (tuning checklist), eyes-gaze never driven by A2F.

Key environment fact: the C++ helper is compiled and the Docker image built ON THE DESKTOP (Windows + WSL2 Ubuntu, SSH `Pavel@100.75.88.35`, SDK at `/root/a2f-sdk/Audio2Face-3D-SDK` in WSL). Repo holds source + scripts; execution tasks sync → build in the TRT container → rebuild image → recreate container → verify via `tools/a2f_probe.py` from the Pi (tunnel `ssh -N -L 18003:localhost:8003 Pavel@100.75.88.35`). MCP SSH has a 30 s cap — long builds run detached (`nohup … &` + log polling).

## Design decisions

### DD-1: A2E integration shape

**Decision:** Interactive classifier + interactive postprocess executors (`nva2e::CreateClassifierEmotionInteractiveExecutor` + `CreatePostProcessEmotionInteractiveExecutor`), created once at startup, run *batch-sequentially before* geometry per utterance: `audioAcc->Close()` → A2E chain `ComputeAllFrames()` fills the existing shared `emoAcc` with timestamped frames → `emoAcc->Close()` → geometry `Invalidate(kLayerAll)` + `ComputeAllFrames()` as today. Classifier shares the existing `audioAcc`; postprocess output feeds the existing `emoAcc` (via `CreateEmotionBinder` if semantics fit, else a results callback doing `emoAcc->Accumulate(r.timeStampCurrentFrame, r.emotions, stream)` per the SDK sample). Bump `emoAcc` capacity 300 → 1800 (A2E ~30 fps × 60 s headroom; 300 = only 10 s).
**Rationale:** One accumulator chain the geometry executor already reads (`main.cpp:119-142`); SDK-sanctioned wiring (`sample-a2f-a2e-executor/main.cpp:275-336`); batch-sequential per-utterance flow avoids the sample's fragile streaming interleave (read-position coupling at :521-563) — our helper only computes after the full utterance arrives anyway.
**Alternative:** Streaming interleave like the SDK sample — rejected: complexity serves realtime streaming we don't do per-utterance.

### DD-2: Tag boost mechanism — native preferred-emotion channel; stdin protocol unchanged

**Decision:** The tag's 10-dim sparse vector (already in the postprocess output space, so the 6→10 `emotionCorrespondence` gap is moot) goes into a new `sharedPreferredEmotionAccumulators` accumulator (`executor_postprocess.h:33-54`) with `PostProcessParams.enablePreferredEmotion=true` + `preferredEmotionStrength` — enabled ONLY when the tag vector is non-zero (an all-zeros neutral tag disables preferred emotion so A2E passes through untouched). Helper still reads `[u32 emotionLen][emotion]` per utterance — `main.cpp`/`engine.py`/`fake_a2f_stream.py` stay wire-identical; zero Python changes; all existing tests pass unmodified. `A2E_ENABLED=0` restores today's exact tag-only path (one-flag rollback, cf. ADR-0013 fallback discipline).
**Rationale:** SDK ships exactly this mechanism; eliminates (not manages) the three-copies-in-sync risk.
**Alternative:** Manual additive combine in the helper — hand-rolled duplication of an SDK feature; kept as documented fallback if preferred-emotion blending turns out to be a pure lerp that suppresses non-tag dims (decided empirically in Task 7). Additive in Python — impossible, Python never sees A2E output.

### DD-3: Knob exposure — env vars parsed in main.cpp, deployment defaults in Dockerfile, code defaults = SDK defaults

**Decision:** Expose now: `A2E_ENABLED` (1), `A2E_MODEL_JSON`, `A2E_EMOTION_STRENGTH` (0.6), `A2E_EMOTION_CONTRAST` (1.0), `A2E_LIVE_BLEND_COEF` (0.7), `A2E_LIVE_TRANSITION_TIME` (0.5), `A2E_MAX_EMOTIONS` (0), `A2E_PREFERRED_STRENGTH` (0.5); face params `A2F_SKIN_STRENGTH`, `A2F_UPPER_FACE_STRENGTH`, `A2F_LOWER_FACE_STRENGTH`, `A2F_BLINK_STRENGTH`; per-blendshape `A2F_BS_MULTIPLIERS` / `A2F_BS_OFFSETS` ("Name=val,Name=val" CSV → `SetMultiplier`/`SetOffset` by ARKit pose name — the BrowDownLeft tool). Stay hardcoded: solver regularization, geometry/classifier `inputStrength`, `inferencesToSkip`, tongue/eyes params (YAGNI).
**Rationale:** `engine.py:132` already forwards the whole environment; env knobs make the tuning pass a `docker run -e` loop with zero recompiles. Follows the `std::getenv` pattern at `main.cpp:50-53` + ENV block `deploy/Dockerfile:26-31`.
**Alternative:** Config file or CLI flags — new plumbing for no gain.

### DD-4: A2E model provisioning is its own task/script — the one external blocker

**Decision:** New `build_a2e_engine.sh` mirroring `build_engine.sh`: `hf download nvidia/Audio2Emotion-v2.2` (HF-gated; `HF_HUB_DISABLE_XET=1`; clear orphaned `.lock` files first) → force bs1 in `trt_info.json` → `audio2emotion-sdk/scripts/gen_sample_data.py` → engine at `_data/generated/audio2emotion-sdk/samples/model/model.json`.
**Rationale:** Weights are ABSENT on the Desktop (interrupted download — only LICENSE+README + orphaned `.lock` files); the #34 spike passed the same HF gate so the token likely still works.
**Alternative:** Bake the ONNX and convert at container start — slower startup, diverges from the James-model pattern.

### DD-5: Tuning = bounded probe-based A/B with pre-registered done-criteria

**Decision:** Fixed harness: #39 utterance (sha-pinned) through `tools/a2f_probe.py`, baseline `run-zeros.json`. Sweep ≤ 3 × `A2E_EMOTION_STRENGTH` {0.6, 0.8, 1.0} × ≤ 2 × `A2E_PREFERRED_STRENGTH` {0.5, 0.7}, plus one BrowDownLeft(+Right) multiplier probe {1.0, 0.5}; ≤ 8 container-restart iterations hard cap. Done means: (a) neutral-tag A2E run beats the zeros baseline (≥ +0.02 abs or ≥ 3× group mean in ≥ 2 expressive groups among brows/eyes-expressive/mouth-form); (b) joy-boost amplifies joy-correlated dims ≥ +20% group-mean over A2E-neutral; (c) BrowDownLeft mean under joy-boost < 0.15 while anger's legit brow-down survives > 50% of untuned; (d) jaw-articulation control within ±25% of A2E-neutral. First config satisfying (a)-(d) wins; else best-scoring ships with the gap documented. Winners baked into Dockerfile ENV.
**Rationale:** #39 set the pre-registered-rule precedent; unbounded sweeps are the failure mode the issue warns about.
**Alternative:** Perceptual-only tuning — unfalsifiable, not comparable to #39 numbers.

### DD-6: Timeouts

**Decision:** Raise `A2F_HELPER_FIRST_TIMEOUT` 30 → 60 s via Dockerfile ENV now (`engine.py:46` already reads it); leave per-utterance `A2F_HELPER_TIMEOUT` at 5 s pending Task 9 measurement (raise ENV only if steady-state exceeds half the budget).
**Rationale:** First call already runs ~30 s against a 30 s budget; A2E engine load pushes it over → spurious kill/respawn loop.
**Alternative:** Raise both blindly — masks a real latency regression.

### DD-7: Python side — no functional changes

**Decision:** `emotion.py`, `server.py`, `engine.py` (code), `tests/fake_a2f_stream.py`, `tts/a2f_fork.py`, `tts_plugin.py`, all frontend: untouched. Only `engine.py` docstring VRAM figure updates in Task 10. The `model_loaded: false` health quirk (`server.py:73`) stays — out of scope.
**Rationale:** DD-2 froze the stdin protocol; WS/DataChannel wire format frozen by the issue.
**Alternative:** —

### DD-8: Verification maps 1:1 to acceptance criteria

**Decision:** Task 7 → criteria 1-2 (A2E-neutral vs zeros baseline; tag amplification); Task 8 → tuning + BrowDownLeft; Task 9 → VRAM figure, restart/supervision survival, reproducible rebuild, timing; Task 11 → test suites + wire format. The `?facedebug=1` human-eye check is flagged for the user in the final report with the replay recipe + new run JSONs.
**Rationale:** Every issue bullet gets named evidence.
**Alternative:** —

### DD-9: Docs

**Decision:** ADR-0016: gate-resolved note (status stays `accepted`), strike the "until the helper work lands" consequence; retro roadmap step 2 → Done + link; `.yoke/context.md` glossary (Audio2Emotion / Expression / Emotion vector) → landed; `a2f_stream/README.md` new lifecycle; `deploy/README.md` + `engine.py` docstring new VRAM figure. Evidence lives in `docs/research/data/40-a2f-emotion-supply/` (run JSONs + analysis.md), no new research doc.
**Rationale:** Matches #39's evidence-in-data-dir pattern.
**Alternative:** Full new research doc — duplication.

## Tasks

### Task 1: A2E model provisioning + bs1 engine on the Desktop

- **Files:** `infra/desktop/a2f/build_a2e_engine.sh` (create); Desktop (off-repo): `/root/a2f-sdk/Audio2Face-3D-SDK/_data/audio2emotion-models/audio2emotion-v2.2/`, `_data/generated/audio2emotion-sdk/`
- **Depends on:** none
- **Scope:** M
- **What:** Write `build_a2e_engine.sh` mirroring `build_engine.sh` (download `nvidia/Audio2Emotion-v2.2`, force bs1 in its `trt_info.json`, generate the TRT engine via `audio2emotion-sdk/scripts/gen_sample_data.py`), sync to the Desktop, run detached inside the TRT container, verify artifacts.
- **How:** Model on `infra/desktop/a2f/build_engine.sh` (bs1-forcing heredoc :23-31, gen_sample_data driver :34-39, container invocation in header comment). `export HF_HUB_DISABLE_XET=1`; clear orphaned `.lock` files under `.cache/huggingface/download/` first; `hf download nvidia/Audio2Emotion-v2.2 --local-dir _data/audio2emotion-models/audio2emotion-v2.2`. Check `hf auth whoami` first — if the HF gate rejects, STOP and report BLOCKED (user must refresh token / accept license). Run in WSL2 via `docker run --rm --device nvidia.com/gpu=all -v /root/a2f-sdk/Audio2Face-3D-SDK:/work -w /work nvcr.io/nvidia/tensorrt:25.08-py3 bash build_a2e_engine.sh`, detached (`nohup … > /root/a2e_engine.log 2>&1 &`) + poll (MCP SSH 30 s cap). Record the exact generated artifact layout for Task 5.
- **Context:** `infra/desktop/a2f/build_engine.sh` (whole), `docs/research/2026-07-03-a2f-3d-spike.md:150-180` (download/gen chain, gates); memory notes: HF Xet hangs → `HF_HUB_DISABLE_XET=1`; long SSH ops → nohup + poll.
- **Verify:** Over SSH: generated `model.json` + `network.trt` exist and are non-trivial (MBs); model dir has `network.onnx`, `model.json`, `config.json`, `network_info.json`, `trt_info.json`; log shows bs1 forced. Commit `build_a2e_engine.sh`.

### Task 2: main.cpp — A2E inference chain + tag as preferred-emotion boost

- **Files:** `infra/desktop/a2f/a2f_stream/main.cpp` (edit), `infra/desktop/a2f/build_helper.sh` (edit)
- **Depends on:** none
- **Scope:** L
- **What:** Integrate A2E per DD-1/DD-2: create classifier + postprocess interactive executors at startup (env `A2E_MODEL_JSON`; `A2E_ENABLED=1` default, `0` = today's tag-only path preserved verbatim); add a preferred-emotion accumulator; per utterance route the stdin tag vector into it (enable preferred emotion only when non-zero); after `audioAcc->Close()` run the A2E chain to fill the shared `emoAcc`, `emoAcc->Close()`, then existing geometry flow. Bump `emoAcc` capacity 300→1800. Add `-I"$SDK/audio2emotion-sdk/include"` to `build_helper.sh`.
- **How:** Follow the executor-creation idiom (`main.cpp:128-155`, Destroyer/ToUniquePtr). Headers: `audio2emotion/audio2emotion.h`, `executor_classifier.h`, `executor_postprocess.h`, `postprocess.h`. `nva2e::ReadClassifierModelInfo`/`ReadPostProcessModelInfo` → `GetExecutorCreationParameters` (mirror the sample's `(60000, 30, 1, 30)` unless headers say otherwise) → interactive variants; classifier shares `audioAccPtr`; postprocess creation params get `sharedPreferredEmotionAccumulators` (new `CreateEmotionAccumulator(emotionSize, 4, 0)`). Prefer `CreateEmotionBinder` (audio2emotion.h:94) for postprocess → `emoAcc`; if binder semantics don't fit (READ the real header over SSH: `/root/a2f-sdk/Audio2Face-3D-SDK/audio2emotion-sdk/include/audio2emotion/`), use a results callback `emoAcc->Accumulate(r.timeStampCurrentFrame, r.emotions, stream)` per sample `:330-336`. Per-utterance: Reset audio/emotion/preferred accumulators; tag → preferred acc + Close; `enablePreferredEmotion` via `IPostProcessor::SetParameters` based on tag non-zero; after audio close: invalidate A2E chain, ComputeAllFrames on it, `emoAcc->Close()`, then geometry as today. Do NOT copy the sample's streaming interleave (:521-563) — batch-sequential per DD-1. stdin/stdout protocol bytes MUST NOT change.
- **Context:** `infra/desktop/a2f/a2f_stream/main.cpp` (whole, 205 lines), `infra/desktop/a2f/build_helper.sh:11-17`, Desktop reference sample `/root/a2f-sdk/Audio2Face-3D-SDK/audio2face-sdk/source/samples/sample-a2f-a2e-executor/main.cpp` (creation :275-298, callback :330-336) and A2E headers (read the real signatures over SSH before writing calls — investigator signatures are summaries).
- **Verify:** Review-level (compiles in Task 4): protocol read/write sites byte-identical; `A2E_ENABLED=0` path is literally the old code path; `git diff` touches only the two files.

### Task 3: main.cpp — env-var knob plumbing

- **Files:** `infra/desktop/a2f/a2f_stream/main.cpp` (edit)
- **Depends on:** Task 2
- **Scope:** M
- **What:** Add the DD-3 knob set: `envFloat(name, default)` / CSV-map helpers; populate `PostProcessParams` from `A2E_*` envs (defaults = SDK defaults per `postprocess.h:44-65`); populate `AnimatorSkinParams` (`skinStrength`, `upperFaceStrength`, `lowerFaceStrength`, `blinkStrength`) from `A2F_*` envs inside the geometry creation params' SkinParameters; parse `A2F_BS_MULTIPLIERS`/`A2F_BS_OFFSETS` and apply via the blendshape solver's `SetMultiplier`/`SetOffset` by ARKit pose name after executor creation.
- **How:** Follow the `modelJson()` getenv pattern (`main.cpp:50-53`). Unset env ⇒ exact SDK default ⇒ zero behavior change when no knob set (except A2E itself). Unknown pose names in CSV: warn to stderr, skip. Keep solver regularization, inputStrength, tongue/eyes hardcoded.
- **Context:** `main.cpp` post-Task-2; Desktop headers `audio2face/animator.h:35-46,169-183`, `audio2face/blendshape_solver.h:72-83,176-196`, `audio2emotion/postprocess.h:44-65`, `audio2face/executor_regression.h:40-56` (read over SSH).
- **Verify:** Review: every knob has an SDK-default fallback; CSV parser survives empty/garbage; no protocol-site edits.

### Task 4: Compile the helper on the Desktop

- **Files:** none in-repo (Desktop: `~/a2f-sdk/a2f_stream/a2f_stream` binary); possible fix iterations on `main.cpp`/`build_helper.sh`
- **Depends on:** Task 3 (Task 1's SDK checkout already present)
- **Scope:** M
- **What:** Sync `main.cpp` + `build_helper.sh` to the Desktop, compile inside the TRT container, land the binary where `build_image.sh:19` expects it (`$SDK/a2f_stream/a2f_stream`).
- **How:** scp both files; run `build_helper.sh` inside the TRT container in WSL2 (nohup + poll if > 20 s). Fix compile errors by iterating the REPO copy (never fork on the Desktop), re-sync, re-run — expect 1-3 signature-mismatch iterations against the real headers.
- **Context:** `infra/desktop/a2f/build_helper.sh` (whole), `infra/desktop/a2f/deploy/build_image.sh:16-25` (artifact path contract), the environment-fact header of this plan.
- **Verify:** g++ exit 0; binary at the `build_image.sh` path; `ldd` resolves inside the container; commit final `main.cpp`/`build_helper.sh` if fixes were needed.

### Task 5: build_image.sh + Dockerfile — bake the A2E model + env defaults

- **Files:** `infra/desktop/a2f/deploy/build_image.sh` (edit), `infra/desktop/a2f/deploy/Dockerfile` (edit)
- **Depends on:** Task 1 (exact generated-A2E dir layout)
- **Scope:** S
- **What:** Stage the generated A2E model dir into the image context (`A2E="${A2E:-…/_data/generated/audio2emotion-sdk/samples/model}"`, add to the MISSING-artifact check, mirror the James `network.onnx` exclusion if applicable); Dockerfile: `COPY a2e/ /opt/a2f/a2e/`, `ENV A2E_MODEL_JSON=/opt/a2f/a2e/model.json A2E_ENABLED=1 A2F_HELPER_FIRST_TIMEOUT=60` + DD-3 knob ENVs at SDK defaults.
- **How:** Follow staging pattern `build_image.sh:24-35` and ENV block `Dockerfile:26-31`; use Task 1's recorded layout.
- **Context:** `deploy/build_image.sh` (whole), `deploy/Dockerfile` (whole), Task 1's artifact layout note.
- **Verify:** `bash -n build_image.sh`; every COPY source staged by the script; ENV names grep-match `main.cpp` getenv strings exactly.

### Task 6: Image rebuild + container redeploy on the Desktop

- **Files:** none in-repo (Desktop: image `voice-agent-a2f:latest`, container `voice-agent-a2f`)
- **Depends on:** Task 4, Task 5
- **Scope:** M
- **What:** Sync repo to the Desktop, run `build_image.sh` in WSL2 (detached + poll), RECREATE the container (plain restart insufficient — image changed) keeping `--restart always` + Scheduled Task supervision, health-check.
- **How:** Runbook `deploy/README.md:9-31`; recreate via `setup_a2f_service.ps1` route or `docker rm -f` + same `docker run --restart always … -p 8003:8003`. Tunnel + `curl http://127.0.0.1:18003/health`.
- **Context:** `deploy/README.md` (whole), `deploy/setup_a2f_service.ps1`.
- **Verify:** `/health` → `{"status":"ok","backend":"helper"}`; container logs clean; one probe smoke run (`tools/a2f_probe.py run --wav docs/research/data/39-a2f-calibration/utterance.wav --preset zeros`) returns ~221 frames (first run may need one warm-up retry within 60 s).

### Task 7: A2E acceptance captures — neutral vs #39 zeros baseline + tag boost

- **Files:** `docs/research/data/40-a2f-emotion-supply/` (create: `run-a2e-neutral.json`, `run-a2e-joyboost.json`, `run-a2e-angerboost.json`, `analysis.md`)
- **Depends on:** Task 6
- **Scope:** M
- **What:** Capture acceptance evidence for issue criteria 1-2: same #39 WAV with (a) zeros vector (= neutral ⇒ pure A2E), (b) joy=1.0, (c) anger=1.0; analyze vs `docs/research/data/39-a2f-calibration/run-zeros.json`, and (b)/(c) vs (a).
- **How:** Tunnel up; warm-up run; `tools/a2f_probe.py run --wav docs/research/data/39-a2f-calibration/utterance.wav --preset zeros|joy|anger`; `analyze` (same wav sha ⇒ comparable). Write `analysis.md` with verbatim analyzer tables + explicit pass/fail: neutral run ≥ +0.02 abs or ≥ 3× in ≥ 2 expressive groups vs #39 zeros; joy-boost ≥ +20% group-mean on joy dims over A2E-neutral. Record BrowDownLeft-under-joy for Task 8. If the boost lerp-suppresses non-tag dims (DD-2 caveat), record it — Task 8 tries `A2E_PREFERRED_STRENGTH` first, manual additive combine (back to Task 2's file) is the last resort.
- **Context:** `tools/a2f_probe.py` (docstring + GROUPS), `docs/research/2026-07-07-a2f-emotion-calibration.md:59-72` (method style), `docs/research/data/39-a2f-calibration/run-zeros.json`.
- **Verify:** Three run JSONs (221 frames, same sha) + `analysis.md` with explicit pass/fail per criterion. This task reports; it does not tune.

### Task 8: Bounded tuning pass + bake final knob values

- **Files:** `infra/desktop/a2f/deploy/Dockerfile` (edit: final ENVs); `docs/research/data/40-a2f-emotion-supply/` (tuning runs + `analysis.md` update); Desktop: container re-runs with `-e`, final image rebuild
- **Depends on:** Task 7
- **Scope:** L
- **What:** Execute DD-5: sweep `A2E_EMOTION_STRENGTH` {0.6, 0.8, 1.0} × `A2E_PREFERRED_STRENGTH` {0.5, 0.7}; BrowDownLeft probe `A2F_BS_MULTIPLIERS="BrowDownLeft=0.5,BrowDownRight=0.5"` (joy AND anger runs — anger's legit brow-down must survive > 50%); pick first config meeting DD-5 (a)-(d) or best-scoring with gap documented; bake winners into Dockerfile ENV; rebuild + recreate (Task 6 recipe); one confirming capture set with the baked image.
- **How:** Each iteration = `docker rm -f voice-agent-a2f && docker run … -e KNOB=val …` + 1-2 probe runs + analyze; ≤ 8 iterations hard cap; label every run JSON with its knob values.
- **Context:** Task 7's `analysis.md`, DD-5 criteria verbatim, `deploy/Dockerfile` ENV block, `tools/a2f_probe.py`.
- **Verify:** Final no-override capture set meets DD-5 (a)-(d) or documents best-effort gap; Dockerfile committed; `analysis.md` records chosen values + sweep table.

### Task 9: VRAM, latency, supervision-survival verification

- **Files:** `docs/research/data/40-a2f-emotion-supply/analysis.md` (ops section); `infra/desktop/a2f/deploy/Dockerfile` (timeout ENV, only if measurement demands)
- **Depends on:** Task 8
- **Scope:** M
- **What:** Measure steady-state VRAM delta (STT+TTS resident, before/after first utterance — method `docs/research/2026-07-03-a2f-3d-spike.md:186-192`, `nvidia-smi` over SSH); time first utterance (< 60 s) and steady-state (< 2.5 s, else raise `A2F_HELPER_TIMEOUT` ENV); verify supervision: `docker restart voice-agent-a2f` → health recovers; `docker inspect` shows `--restart always`; Scheduled Task `voice-agent-a2f-boot` still registered.
- **How:** All over SSH from the Pi; probe timing through the tunnel (wall-clock the run subcommand).
- **Context:** `docs/research/2026-07-03-a2f-3d-spike.md:186-192`, `deploy/README.md`.
- **Verify:** Documented in `analysis.md`: VRAM delta (~0.5 GB order expected — the figure is the deliverable), first < 60 s, steady < 2.5 s, restart-survival confirmed.

### Task 10: Documentation sweep

- **Files:** `docs/adr/0016-a2f-emotion-supply.md`, `docs/retro-epic8-a2f.md`, `.yoke/context.md`, `infra/desktop/a2f/a2f_stream/README.md`, `infra/desktop/a2f/deploy/README.md`, `infra/desktop/a2f/engine.py` (docstring VRAM figure only)
- **Depends on:** Task 9
- **Scope:** M
- **What:** ADR-0016: gate-resolved note (2026-07-07, proceed branch, link calibration report, landed in #40, scope unshifted; strike "until the helper work lands" consequence). Retro roadmap step 2 → Done + link to `docs/research/data/40-a2f-emotion-supply/`. Glossary: Audio2Emotion / Expression / Emotion vector → landed (A2E baseline + tag boost). `a2f_stream/README.md`: new lifecycle (A2E chain, preferred-emotion boost, env knobs, `A2E_ENABLED=0` fallback). `deploy/README.md` + `engine.py` docstring: new VRAM figure + A2E model artifact in build inputs. All English.
- **How:** Surgical edits matching each doc's voice; numbers from Task 9.
- **Context:** The six files; glossary entries `.yoke/context.md:60-72,97-105`; retro roadmap `docs/retro-epic8-a2f.md:75-85`.
- **Verify:** Grep: no remaining "does not run it yet / decided-but-pending" A2E claims in touched docs; links resolve.

### Task 11: Validation

- **Files:** —
- **Depends on:** all
- **Scope:** M
- **What:** Full acceptance sweep against issue #40's criteria.
- **How:** On the Pi: `python -m pytest infra/desktop/a2f/tests/ -q` (must pass UNMODIFIED — proves the stdin protocol froze) and `node --test infra/pi/web/static/js/*.test.mjs`. Diff check: no `emotion.py`/`server.py`/`engine.py`-code/`fake_a2f_stream.py`/frontend changes beyond the engine.py docstring. Wire-format spot check: one captured frame has exactly `{type, frame, t, arkit}`. Health via tunnel. Cross-check every issue acceptance bullet against Tasks 7-9 evidence; flag the human-eye `?facedebug=1` item for the user with the replay recipe (`docs/research/2026-07-07-a2f-emotion-calibration.md:171-189`) + new run JSONs.
- **Verify:** Both suites green; per-criterion evidence checklist in the final report; deviations listed explicitly.

## Execution

- **Mode:** sub-agents
- **Parallel:** true
- **Reasoning:** 11 tasks; only T1 ∥ T2 touch disjoint resources (T1 mostly detached waiting); everything from T4 on either chains through `main.cpp`/`Dockerfile` or occupies the single Desktop GPU/docker — strictly sequential.
- **Order:**
  Group 1 (parallel): Task 1, Task 2
  ─── barrier ───
  Group 2 (sequential): Task 3 → Task 4
  (Task 5 may run after Task 1 completes, before/alongside Task 4 — disjoint files; never two Desktop-op tasks concurrently)
  ─── barrier ───
  Group 3 (sequential): Task 5 → Task 6 → Task 7 → Task 8 → Task 9 → Task 10 → Task 11

## Verification

(From issue #40, unchanged)

- With no emotion tag (`neutral`), an emotionally-voiced utterance produces a visibly non-zero emotion vector and measurably richer brow/eye/mouth-form blendshape amplitudes than today's baseline (compare against the calibration report's zero-vector numbers).
- An explicit tag (e.g. `[emotion:happy]`) visibly amplifies the corresponding dimensions on top of the A2E baseline.
- `?facedebug=1` on a live reply shows clearly richer raw-face mimicry than the pre-change captures from the calibration issue (side-by-side).
- Helper container rebuilds reproducibly and survives the existing restart/supervision setup; VRAM stays within budget (~0.5 GB order — document the new figure).
- Existing helper tests (`infra/desktop/a2f/tests/`) pass; wire format unchanged (frontend tests `node --test infra/pi/web/static/js/*.test.mjs` stay green untouched).
- ADR-0016 status/notes updated if scope shifted per the calibration verdict.

## Materials

- `docs/adr/0016-a2f-emotion-supply.md` — the decision (read first).
- `docs/retro-epic8-a2f.md` — root-cause chain and roadmap (step 2).
- `docs/adr/0015-a2f-helper-production.md` — helper deployment/supervision constraints.
- Code: `infra/desktop/a2f/a2f_stream/main.cpp` (helper), `infra/desktop/a2f/emotion.py` (enum→vector), `infra/desktop/a2f/engine.py` + `server.py` (service), `infra/pi/agent/tts_plugin.py` (tag → per-sentence emotion field), `infra/desktop/a2f/deploy/` (image build).
- Glossary: `.yoke/context.md` — Audio2Emotion, Emotion vector, Emotion tag.
- #39 calibration: `docs/research/2026-07-07-a2f-emotion-calibration.md` + `docs/research/data/39-a2f-calibration/` (verdict: proceed; baselines for A/B).

## Open questions

1. **HF gate for `nvidia/Audio2Emotion-v2.2` (Task 1, potential blocker):** weights absent on the Desktop (interrupted download); the #34 spike passed the same gate so the token should still work — if `hf download` is rejected, Task 1 reports BLOCKED and needs the user to refresh the token / re-accept the license. Isolated in Task 1; only the Task 5+ chain stalls.
