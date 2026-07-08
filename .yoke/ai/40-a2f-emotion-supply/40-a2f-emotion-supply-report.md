# Report: 40-a2f-emotion-supply

**Plan:** .yoke/ai/40-a2f-emotion-supply/40-a2f-emotion-supply-plan.md
**Mode:** sub-agents
**Status:** ✅ complete

## Tasks

| #   | Task                                            | Status                | Commit              | Concerns                       |
| --- | ----------------------------------------------- | --------------------- | ------------------- | ------------------------------ |
| 1   | A2E model provisioning + bs1 engine (Desktop)   | ✅ DONE (after unblock) | `4746e69`         | HF gate blocker, user resolved |
| 2   | main.cpp — A2E chain + preferred-emotion boost  | ✅ DONE               | `064b4d2`           | 2 header-forced deviations, see below |
| 3   | main.cpp — env-var knob plumbing                | ✅ DONE               | `dd48752`           | —                              |
| 4   | Compile helper on Desktop                       | ✅ DONE               | — (no repo changes) | 0 fix iterations               |
| 5   | build_image.sh + Dockerfile — bake A2E model    | ✅ DONE               | `603d31d`+`8eaedae` | Critical review fix, see below |
| 6   | Image rebuild + container redeploy              | ✅ DONE               | — (ops)             | CRLF + nohup gotchas, see below |
| 7   | A2E acceptance captures vs #39 baselines        | ✅ DONE               | `69f009e`           | criterion (a) FAIL at defaults → routed to T8 |
| 8   | Bounded tuning + bake final knobs               | ✅ DONE               | `6c7765b`+`aaa1df4` | SDK bug found, see below       |
| 9   | VRAM / latency / supervision verification       | ✅ DONE               | `4f309df`           | VRAM 1.65 GiB > issue hint, documented |
| 10  | Documentation sweep                             | ✅ DONE               | `77dd375`           | —                              |
| 11  | Validation                                      | ✅ DONE               | —                   | —                              |

All 11 tasks reviewed: Tasks 1, 2, 3, 5, 7, 8, 9, 10 ✅ approved by task-reviewer (T5 after one Critical fix iteration); Tasks 4, 6, 11 are ops/validation tasks with no repo diff to review (outcomes verified live).

## Post-implementation

| Step          | Status                            | Commit |
| ------------- | --------------------------------- | ------ |
| Validate      | ✅ pass (T11 full sweep on HEAD)  | —      |
| Documentation | ⏭️ skipped (`--update-docs` not set; docs shipped in-plan as Task 10) | — |
| Format        | ✅ done (no formatter configured) | —      |

## Outcome

**ADR-0016 implemented and live in production.** The A2F helper now runs Audio2Emotion (A2E) on every utterance's PCM as the baseline emotion source; the LLM emotion tag became an additive boost via the SDK's native preferred-emotion channel (enabled only for non-zero tags; zero/neutral tag ⇒ pure A2E). Wire format (`{type, frame, t, arkit}`), stdin protocol, and all Python service code unchanged — `engine.py`/`fake_a2f_stream.py`/tests untouched (engine.py docstring only). `A2E_ENABLED=0` is a one-flag rollback.

**Acceptance criteria (issue #40):**
- (i) Neutral tag ⇒ non-zero, richer face: **PASS with tuning caveat** — pure A2E at shipped defaults FAILED the pre-registered bar (1 of 3 groups); the baked winner (brow multipliers `browInnerUp=1.35, browDownLeft/Right=1.25`, A2E knobs at model defaults) passes (brows +0.0225 abs / 2.20×, eyes-expressive 30.49×) — confirmed on the production image.
- (ii) Explicit tag amplifies on top of A2E: **PASS** (joy-boost: brows +38.3%, mouth-form +36.8% over pure A2E).
- (iii) `?facedebug=1` side-by-side: **flagged for user** (human-eye item) — replay recipe: `docs/research/2026-07-07-a2f-emotion-calibration.md:171-189`, inject the run JSONs from `docs/research/data/40-a2f-emotion-supply/`.
- (iv) Reproducible rebuild + supervision + VRAM: **PASS** — new `build_a2e_engine.sh`; supervision 4/4 checks incl. transparent in-container crash-respawn; VRAM documented.
- (v) Helper tests pass (7/7 unmodified), wire format unchanged, frontend tests green (6/6, 75 assertions): **PASS**.
- (vi) ADR-0016 updated: **PASS** (`77dd375` — status note, consequences, glossary, retro roadmap step 2 Done).

## Concerns

### Task 2: header-forced deviations from DD-1 (both reviewed and approved)
The real SDK fuses classification + post-processing (incl. preferred-emotion blending) inside the classifier interactive executor — one executor, not two; `CreateEmotionBinder` is incompatible with interactive executors — results-callback wiring used (DD-1's own fallback).

### Task 5: Critical review catch
The plan's DD-3 wrongly took `maxEmotions=0` as the SDK default "no cap". In SDK source, maxEmotions means "keep the N largest" — 0 zeroes ALL A2E output every frame. The pin was dropped (`8eaedae`); model config's 6 rules.

### Task 8: real SDK bug discovered
`A2E_MAX_EMOTIONS>6` hangs the helper forever (unsigned underflow `6 - maxEmotions` in the SDK CUDA post-process kernel — infinite loop; verified live). Production was briefly (~10 min) in this state during iteration 3; fully recovered. Documented in Dockerfile comment + analysis.md. Also: the A2E network classifies only SIX emotions, so Task 7's truncation hypothesis for boost suppression was disproved.

### Soft goal unmet (documented, not hidden)
The tag boost suppresses non-tag A2E dims (eyes-expressive 0.22× under joy-boost) — a property of the SDK's preferred-emotion lerp + geometry nonlinearity, unreachable by the exposed knobs. Per-config suppression ratios recorded in analysis.md.

### VRAM deviation
Helper total ~1.65 GiB (A2E TRT engine ≈1.25 GiB of it) vs the issue's "~0.5 GB order" hint — ~3.3–4.2× higher; recorded as the new number of record with headroom math (projected all-services ≈7.2 of 12 GiB, 59%).

### Ops gotchas discovered (recorded in memory + deploy README)
Desktop checkout `core.autocrlf=true` re-smudges scripts to CRLF on every pull (breaks bash in WSL/containers) — proper fix is an in-repo `.gitattributes` forcing LF (recommended follow-up). Detached WSL jobs die with the wsl.exe session — use `systemd-run`.

## Validation

python -m pytest infra/desktop/a2f/tests/ -q ✅ (7 passed — unmodified tests, proves the stdin protocol froze)
node --test infra/pi/web/static/js/*.test.mjs ✅ (6/6 tests, 75 assertions)
Diff scope 9f6470d..HEAD ✅ (no changes to emotion.py/server.py/fakes/tests/tts/agent/frontend; engine.py docstring-only)
Wire format ✅ (all 221 captured frames exactly `{type, frame, t, arkit}`)
Live /health via tunnel ✅ (`status:ok, backend:helper, device:cuda`)
Helper compile (Desktop, TRT container) ✅ (first attempt, ldd clean)

## Changes summary

| File | Action | Description |
| ---- | ------ | ----------- |
| infra/desktop/a2f/a2f_stream/main.cpp | modified (+286) | A2E chain (fused classifier interactive executor → shared emoAcc), preferred-emotion tag boost, env knobs, A2E_ENABLED=0 fallback |
| infra/desktop/a2f/build_helper.sh | modified | audio2emotion-sdk include path |
| infra/desktop/a2f/build_a2e_engine.sh | created | A2E model download (HF-gated) + bs1 TRT engine generation |
| infra/desktop/a2f/deploy/build_image.sh | modified | stage A2E model dir (network.onnx excluded, −1.27 GB) |
| infra/desktop/a2f/deploy/Dockerfile | modified | COPY a2e/, ENV knob defaults, FIRST_TIMEOUT=60, tuned brow multipliers, MAX_EMOTIONS trap comment |
| infra/desktop/a2f/engine.py | modified | docstring only (VRAM figure, A2E description) |
| docs/research/data/40-a2f-emotion-supply/ | created | 27 run JSONs (acceptance + tuning sweep + baked confirm) + analysis.md (criteria, tuning table, ops verification) |
| docs/adr/0016-a2f-emotion-supply.md | modified | status update: landed in #40 |
| docs/retro-epic8-a2f.md | modified | roadmap step 2 → Done 2026-07-08 |
| .yoke/context.md | modified | glossary: Audio2Emotion/Emotion vector/Emotion tag/Expression → landed |
| infra/desktop/a2f/a2f_stream/README.md | modified | lifecycle, env-knob table, footprint |
| infra/desktop/a2f/deploy/README.md | modified | A2E build input, VRAM, CRLF gotcha |

Desktop (off-repo): A2E model + TRT engine at `_data/generated/audio2emotion-sdk/samples/model/`; helper binary rebuilt; image `voice-agent-a2f:latest` (5f5fcdda6b2a) live with `--restart always` + Scheduled Task `voice-agent-a2f-boot`.

## Commits

- `9f6470d` #40 docs(40-a2f-emotion-supply): add implementation plan
- `4746e69` #40 feat(40-a2f-emotion-supply): add a2e model download + trt engine build script
- `064b4d2` #40 feat(40-a2f-emotion-supply): a2e inference chain + preferred-emotion tag boost in helper
- `dd48752` #40 feat(40-a2f-emotion-supply): env-var knobs for a2e and face params
- `603d31d` #40 feat(40-a2f-emotion-supply): bake a2e model + env defaults into helper image
- `8eaedae` #40 fix(40-a2f-emotion-supply): drop A2E_MAX_EMOTIONS pin that zeroed a2e output
- `69f009e` #40 feat(40-a2f-emotion-supply): a2e acceptance captures vs #39 baselines
- `6c7765b` #40 feat(40-a2f-emotion-supply): bake tuned emotion knobs
- `aaa1df4` #40 docs(40-a2f-emotion-supply): tuning sweep data + Tuning section (Task 8)
- `4f309df` #40 docs(40-a2f-emotion-supply): ops verification (vram, latency, supervision)
- `77dd375` #40 docs(40-a2f-emotion-supply): adr/retro/glossary/readme updates for landed a2e

Note: commits up to `77dd375` were pushed to origin/main during execution (the Desktop deploys via `git pull` — push was operationally required by Tasks 6/8/9/10).

## Follow-ups (recommended, out of scope)

- Add `.gitattributes` forcing LF for `*.sh`/`Dockerfile`/`*.py` — kills the Desktop CRLF trap at the root.
- `?facedebug=1` human-eye check (acceptance iii) — replay recipe + JSONs linked above.
- #41 (Live2D amplification) remains necessary: absolute eye amplitudes stay modest (per #39/#40 data); the boost-suppression finding strengthens the case for frontend-side accents.
