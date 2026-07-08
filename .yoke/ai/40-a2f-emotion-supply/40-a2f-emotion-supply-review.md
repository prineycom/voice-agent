# Code Review: 40-a2f-emotion-supply

## Summary

### Context and goal

Issue #40 (ADR-0016): the A2F helper's emotion input was tag-only and usually all-zeros, so the face degenerated to lip-sync. This changeset integrates Audio2Emotion (A2E) into the C++ helper as the per-utterance baseline emotion source, demotes the LLM emotion tag to an additive boost via the SDK's preferred-emotion channel, adds env-var tuning knobs, bakes the A2E TRT engine plus tuned brow multipliers into the production image, and documents acceptance/tuning/ops evidence.

### Key code areas for review

1. **`infra/desktop/a2f/a2f_stream/main.cpp:411-480` (utterance loop)** — the per-utterance state machine changed: emoAcc stays OPEN through the audio read, is filled by the A2E pass, degrades to tag-only if the pass fails/empties; ordering (prefAcc close → audio close → A2E Invalidate+ComputeAllFrames → emoAcc close → geometry) is the correctness core.
2. **`main.cpp:337-405` (A2E executor creation)** — fused classifier interactive executor (header-forced deviation from the plan's two-executor DD-1), emotion-size guard, preferred accumulator wiring, env-knob application, fail-fast on missing model.
3. **`main.cpp:88-186` / `env_knobs.h`** — envFloat/envInt/envCsvMap/applyBsEnvOverrides: warn-and-degrade semantics, CSV robustness; SDK-independent parts now extracted and unit-tested.
4. **`main.cpp:235-238 onEmotions()`** — A2E→emoAcc callback on `r.cudaStream`; same stream as geometry, no cross-stream hazard.
5. **`infra/desktop/a2f/deploy/Dockerfile:35-67`** — baked A2E model, knob defaults, `A2F_HELPER_FIRST_TIMEOUT=60`, tuned `A2F_BS_MULTIPLIERS`, MAX_EMOTIONS trap comment.
6. **`infra/desktop/a2f/deploy/build_image.sh`** — A2E artifact preflight + staging (ONNX excluded, −1.27 GB).
7. **`infra/desktop/a2f/build_a2e_engine.sh`** — HF-gated model download + TRT engine build; lock-file cleanup for interrupted-download reruns.
8. **`docs/research/data/40-a2f-emotion-supply/analysis.md`** — pre-registered acceptance criteria, tuning sweep, VRAM/latency/supervision evidence.

### Complex decisions

1. **Fused A2E executor, not the planned classifier+postprocess pair** (`main.cpp:337-343`) — the SDK's classifier interactive executor already contains post-processing; the separate post-process executor is an alternative source (zero-fills inference input), not a downstream stage. Correct call; documented in-code.
2. **Tag boost via SDK preferred-emotion lerp, toggled per utterance only for non-zero tags** (`main.cpp:417-431`) — avoids hand-rolled blending, but the lerp mechanically suppresses non-tag A2E dims (documented soft-goal miss).
3. **Per-pose multipliers/offsets applied at executor creation, not runtime** (`main.cpp:148-186`) — runtime setters live on a solver object the interactive executor never exposes; retuning requires a container restart (acceptable: tuning loop is `docker run -e`).
4. **Fail-fast (exit 2) when A2E model missing with A2E enabled** — originally fired only after the multi-second engine load; review fix moved it to the top of `main()` (milliseconds).
5. **emoAcc capacity 300→1800** (`main.cpp:259-262`) — 60 s A2E headroom per utterance; beyond that the A2E pass keeps partial frames (now documented in the helper README).

### Questions for the reviewer

1. ~~Is fail-hard-at-spawn the right posture for a missing A2E model?~~ — addressed by review fix #2 (early fail-fast).
2. ~~Should the MAX_EMOTIONS hang-trap be guarded in code, not just a comment?~~ — addressed by review fix #1 (config-bound clamp).

### Risks and impact

- ~~Misconfigured `A2E_MAX_EMOTIONS>6` hangs the helper~~ — clamped in code as of `7904b89`; verified live post-redeploy.
- VRAM grows 0.4→1.65 GiB (documented deviation, headroom math recorded: projected all-services ≈59% of 12 GiB); a future model swap could squeeze coexistence with STT/TTS.
- Rollback is one flag (`A2E_ENABLED=0`, model file then not required); stdin/stdout protocol byte-identical — low blast radius.
- Most commits were pushed to origin/main mid-execution (deploy pulls from main), so review fixes land as follow-up commits, not amends.

### Tests and manual checks

**Auto-tests:**
- Python helper-protocol tests pass unmodified (7/7) — proves the stdin protocol froze; frontend 6/6 (75 assertions); new `test_env_knobs.cpp` 30/30 on the Pi (first C++ unit test in the repo).
- No automated tests exercise the GPU/SDK C++ paths (pre-existing project gap, now narrowed to SDK-typed code only).

**Manual scenarios:**
1. `?facedebug=1` side-by-side vs #39 captures (acceptance criterion iii) → human-eye confirmation of richer mimicry — replay recipe: `docs/research/2026-07-07-a2f-emotion-calibration.md:171-189`, inject JSONs from `docs/research/data/40-a2f-emotion-supply/`.
2. Live reply with `[emotion:happy]` tag → visible amplification on top of the A2E baseline.

### Out of scope

- `server.py:73` `model_loaded: false` health quirk (pre-existing, DD-7).
- Live2D amplification (#41), head-sway — retro roadmap steps 3-4.
- Python service code, wire formats, emotion.py mapping — deliberately frozen.
- Boost suppression of non-tag A2E dims — SDK lerp property, unreachable by exposed knobs (documented in analysis.md).

## Commits

| Hash    | Description |
| ------- | ----------- |
| 9f6470d | docs: add implementation plan |
| 4746e69 | feat: add a2e model download + trt engine build script |
| 064b4d2 | feat: a2e inference chain + preferred-emotion tag boost in helper |
| dd48752 | feat: env-var knobs for a2e and face params |
| 603d31d | feat: bake a2e model + env defaults into helper image |
| 8eaedae | fix: drop A2E_MAX_EMOTIONS pin that zeroed a2e output |
| 69f009e | feat: a2e acceptance captures vs #39 baselines |
| 6c7765b | feat: bake tuned emotion knobs |
| aaa1df4 | docs: tuning sweep data + Tuning section (Task 8) |
| 4f309df | docs: ops verification (vram, latency, supervision) |
| 77dd375 | docs: adr/retro/glossary/readme updates for landed a2e |
| 6a04a33 | docs: add execution report |
| 7904b89 | fix: fix 5 review issues |

## Changed Files

41 files, +1556/−48 (base `2476dd3`). Core: `infra/desktop/a2f/a2f_stream/main.cpp` (+286 pre-fix), new `env_knobs.h` + `test_env_knobs.cpp`, `build_a2e_engine.sh` (new), `deploy/Dockerfile` (+34), `deploy/build_image.sh`, docs (ADR-0016/retro/glossary/READMEs), 27 research JSONs + analysis.md, `.gitattributes` (new), yoke artifacts.

## Issues Found

| Severity  | Score | Category | File:line | Description |
| --------- | ----- | -------- | --------- | ----------- |
| Important | 60 | bugs | main.cpp:386-391 | `A2E_MAX_EMOTIONS>6` accepted silently despite the documented unsigned-underflow infinite GPU loop (live 10-min incident in Task 8); guard existed only as a Dockerfile comment |
| Minor | 40 | quality | main.cpp:348-350 | Missing A2E model with A2E enabled fails only after the multi-second engine load; lazy respawn repeats load-then-die per utterance while `/health` stays ok |
| Minor | 35 | tests | main.cpp:88-186 | New GPU-independent env/CSV logic locked in main.cpp's anonymous namespace — testable on any host but untested |
| Minor | 30 | quality | deploy/README.md:26-28 | CRLF trap handled by documenting a manual sed workaround instead of shipping the 2-line `.gitattributes` root fix |
| Minor | 20 | bugs | main.cpp:259-262,467 | Utterances >~60 s overflow the 1800-frame emotion accumulator; graceful partial-frames degradation invisible outside a code comment |

12 previously-recorded issues (execution-report Concerns + per-task review minors) were excluded from re-reporting.

## Fixed Issues

| Issue | Commit | Description |
| ----- | ------ | ----------- |
| MAX_EMOTIONS>6 silent acceptance | `7904b89` | Clamped to the pre-override model-config value with a stderr warning (bound taken from config, not hardcoded 6); verified live post-redeploy (`-e A2E_MAX_EMOTIONS=10` warns and works instead of hanging) |
| Slow fail on missing A2E model | `7904b89` | Fail-fast `fopen` check as the first statement of `main()` — spawn dies in milliseconds; later ReadClassifierModelInfo check kept as belt & braces |
| Untested env/CSV helpers | `7904b89` | Pure-move extraction into header-only `env_knobs.h` (byte-identical semantics); new framework-free `test_env_knobs.cpp` — 30 assertions green on the Pi; run instructions documented in build_helper.sh |
| CRLF workaround instead of root fix | `7904b89` | Repo-root `.gitattributes` (`*.sh`/`*.py`/`*.service`/`Dockerfile` → LF); deploy README gotcha shrunk to a pointer + one-time renormalize recipe for smudged checkouts |
| Undocumented 60 s A2E ceiling | `7904b89` | Documented in a2f_stream/README.md lifecycle (ceiling + graceful partial-frames degradation); MAX_EMOTIONS trap note updated to reflect the new startup clamp |

## Skipped Issues

**All found issues were fixed.**

## Post-fix validation

- `pytest infra/desktop/a2f/tests/` ✅ 7/7 (unmodified — stdin protocol still frozen)
- `node --test infra/pi/web/static/js/*.test.mjs` ✅ 6/6, 75 assertions
- `test_env_knobs` ✅ 30/30 (g++ 14.2.0, Pi)
- main.cpp compile check on Desktop (TRT container) ✅ first attempt
- Formatter: none configured in the project (no-op)
- Desktop redeploy with the fixed helper: image rebuilt, container recreated, health + smoke + live clamp regression test — see final state below

## Recommendations

- Run the `?facedebug=1` human-eye check (acceptance criterion iii) — the only unverified acceptance item.
- Proceed with #41 (Live2D amplification): absolute eye amplitudes remain modest and the boost-suppression finding strengthens the case for frontend-side accents.
- Desktop checkouts already smudged by CRLF need the one-time renormalize after pulling `.gitattributes` (recipe in deploy/README.md).
- Consider a lightweight C++ test for the per-utterance state machine if the helper grows further (the env_knobs test sets the pattern).
