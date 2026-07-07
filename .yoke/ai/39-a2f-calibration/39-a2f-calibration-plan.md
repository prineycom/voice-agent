# Plan: 39-a2f-calibration

**Issue:** https://github.com/prineycom/voice-agent/issues/39
**Mode:** sub-agents
**Parallel:** false

A2F calibration: forced-emotion vs zero-vector A/B on the raw blendshape stream. The gate experiment for the Epic 8 retro roadmap (`docs/retro-epic8-a2f.md`, step 1): run the same utterance through production A2F (`:8003`) with zeros / joy=1.0 / anger=1.0 emotion vectors, quantify per-group blendshape amplitude deltas, and record the verdict that scopes issue #40 (emotion supply vs SDK tuning first).

## Verified facts (from investigation)

- **WS protocol** (`infra/desktop/a2f/server.py:9-16,79-109`): TEXT `{"emotion": [10 floats]}` (raw list accepted verbatim, `server.py:44-51`) → BINARY PCM16 LE **24 kHz** mono (server resamples internally, `engine.py:186`; chunking irrelevant, server concatenates) → TEXT `{"end": true}` → per-frame TEXT `{"type":"blendshapes","frame":i,"t":i/30,"arkit":{68 keys: ARKIT_52+TONGUE_16}}` → `{"done": true}` or `{"error": ...}`.
- **Emotion vector order** (`infra/desktop/a2f/emotion.py:13-16`): `[grief, joy, disgust, outofbreath, pain, anger, amazement, cheekiness, sadness, fear]`. joy = index 1, anger = index 5. Anger is unreachable via the enum path — raw list required.
- **Connectivity**: `:8003` NOT reachable from Pi over Tailscale (WSL2-internal). Workaround: `ssh -f -N -L 18003:127.0.0.1:8003 Pavel@100.75.88.35` → `ws://127.0.0.1:18003/a2f`. `:8002` (TTS) IS reachable directly.
- **Mock hazard**: MockBackend fakes emotion response from `emotion[1]/[2]` (`engine.py:70-71`) — a probe against mock would fake a positive result. Hard health gate required (`/health` → `backend == "helper"`).
- **TTS capture caveat**: the TTS socket tees to A2F and interleaves blendshape/`a2f_done` TEXT frames (`infra/desktop/tts/server.py:157-158,217`) — WAV capture keeps only BINARY messages.
- **Timing**: helper per-utterance timeout 5 s, 30 s for the first utterance after spawn — probe recv timeout ~60 s. Helper is single-flight-locked — run while the agent is idle.
- **Environment**: probe runs on the Pi with `infra/pi/agent/.venv/bin/python` (`websockets==15.0.1`, `numpy`; no soundfile → stdlib `wave`; no fastapi → no local mock dry-run). No `tools/` dir yet.
- **Replay**: `window.__a2fInject(evt)` (`blendshapes.js:97-102`) routes through the wire `handle()`; synchronous bulk push plays at real speed via the scheduler; `{done:true}` ends; re-pick auto-re-anchors. Frames stored verbatim are directly injectable.

## Design decisions

### DD-1: One probe script, three subcommands — `tools/a2f_probe.py tts | run | analyze`
- **Decision**: durable calibration tooling in `tools/` (new dir), single file, subcommands separate authoring/capture/analysis with independent Verify.
- **Rationale**: will be re-run after ADR-0016 lands; issue allows `tools/`. Zero new deps (websockets + numpy + stdlib). WS client mirrors `infra/desktop/tts/a2f_fork.py:103-131`.
- **Alternative**: throwaway script in `.yoke/ai/` — rejected: the tool outlives this issue (re-calibration after #40).

### DD-2: Emotion input = raw positional 10-float list, presets + override
- **Decision**: `--preset zeros|joy|anger` plus `--vector` / `--emotion joy=1.0` overrides; A2E order hardcoded with a citation comment (no import from `infra/desktop/` — probe is standalone on the Pi).
- **Rationale**: anger unreachable via enum (`emotion.py:19-25`); `server.py:49-50` accepts lists verbatim.
- **Alternative**: reuse the enum path — rejected: cannot express anger or arbitrary vectors.

### DD-3: Hard health gate before every capture
- **Decision**: `run` aborts unless `/health` reports `backend == "helper"` (`--allow-mock` escape hatch); health snapshot embedded in run meta.
- **Rationale**: mock backend fakes emotion→face response (`engine.py:70-71`) — silent false positive would corrupt the verdict that scopes #40.
- **Alternative**: trust the operator — rejected: the failure mode is silent and invalidates the experiment.

### DD-4: Captured runs committed as JSON at `docs/research/data/39-a2f-calibration/`
- **Decision**: one file per run `{"meta": {...}, "frames": [verbatim wire frames]}`; `meta` = utterance text, WAV sha256+duration, emotion label+vector, health snapshot, URL, timestamp, TTS engine/voice. The fixed `utterance.wav` is committed too (~340 KB). Total ~1.5-2 MB.
- **Rationale**: verbatim frames are directly `__a2fInject`-able; report and data must co-survive in `docs/`; experiment repeatable only with pinned input.
- **Alternative**: `.yoke/ai/` artifacts — rejected: report cites the data; process artifacts and evidence have different lifetimes.

### DD-5: Grouping refines eyes/lids into three sub-buckets
- **Decision**: groups per `arkit-map.js:16-34` — brows (5), mouth-form (5), cheeks (2) — with eyes split: eyes-expressive (EyeWide×2, EyeSquint×2), eyes-blink (EyeBlink×2), eyes-gaze (EyeLook×8); plus control group jaw-articulation (JawOpen, JawForward, JawLeft, JawRight, MouthClose) expected ~stable across runs. TONGUE_16 excluded (count only). Metrics per key: mean/max/std; per group: mean-of-means, max-of-maxes, mean-of-stds; deltas vs zeros as absolute Δ and ratio. `analyze` emits markdown tables.
- **Rationale**: EyeWide/Squint carry emotion; blink is periodic noise; gaze is not emotion. Jaw control validates runs are comparable (audio drove the model).
- **Alternative**: flat 4-group split as in the issue — rejected: blink noise would drown the expressive-eyes signal.

### DD-6: Pre-registered decision rule, stated before the numbers
- **Decision**: verdict = "emotion supply (ADR-0016) proceeds" iff joy or anger produces, in ≥1 expressive group (brows, mouth-form, cheeks, eyes-expressive), a group-mean amplitude increase ≥ 0.05 absolute OR ≥ 3× the zeros baseline; otherwise "SDK tuning first". Borderline → facedebug visual as tiebreaker.
- **Rationale**: the verdict directly scopes #40; pre-registration prevents post-hoc fitting.
- **Alternative**: eyeball-only judgment — rejected: not reproducible, invites bias.

### DD-7: Replay recipe = DevTools file-picker snippet, zero frontend changes
- **Decision**: recipe pastes an `<input type=file>` snippet into the console; reads a run JSON; `run.frames.forEach(f => __a2fInject(f)); __a2fInject({done:true})`.
- **Rationale**: `fetch()` of repo files fails cross-origin/mixed-content; `__a2fInject` + scheduler already play bulk-pushed frames at real speed (`blendshapes.js:97-102`).
- **Alternative**: serve data from the web root — rejected: frontend/deploy changes out of scope.

### DD-8: Live-capture operational recipe
- **Decision**: preflight agent-idle check → SSH tunnel `18003→127.0.0.1:8003` → health gate → `tts` capture of the fixed utterance (`ws://100.75.88.35:8002/tts`, `{"text", "voice":"default", "emotion":"neutral"}`, binary-only) → three `run` captures → kill tunnel. Default text (configurable `--text`), emotionally chargeable, ~6-7 s: "No — no, this can't be happening. After everything we built together, you're telling me it's all gone? That is absolutely unbelievable."
- **Rationale**: only path to `:8003` from the Pi; TTS capture triggers one harmless extra A2F run via the production tee (expected).
- **Alternative**: Windows portproxy for 8003 — rejected: host change violates the read-only constraint.

## Tasks

### Task 1: Author `tools/a2f_probe.py`
- **Files:** `tools/a2f_probe.py` (create; creates `tools/`)
- **Depends on:** none
- **Scope:** M
- **What:** Single-file probe with `tts`, `run`, `analyze` subcommands per DD-1..DD-5, DD-8.
- **How:** `tts`: connect `ws://100.75.88.35:8002/tts` (arg `--url`), send `{"text","voice","emotion":"neutral"}`, keep only BINARY messages until `{done:true}` TEXT (discard interleaved blendshape/`a2f_done` TEXT frames), write 24 kHz mono PCM16 WAV via stdlib `wave`, print duration, abort outside 4-10 s. `run`: GET `/health` (derive http URL from ws URL), abort unless `backend=="helper"` (`--allow-mock`), connect `/a2f`, send `{"emotion": [10 floats]}` from `--preset zeros|joy|anger` / `--vector` / `--emotion k=v`, stream WAV as PCM chunks, send `{"end":true}`, collect frames until `{done}`/`{"error"}` (recv timeout 60 s), write `{"meta","frames"}` JSON via `--out`. `analyze`: load N run JSONs + `--baseline`, assert identical `meta.wav_sha256`, compute per-key mean/max/std and per-group aggregates for the 7 groups of DD-5, emit markdown tables with Δ and ratio vs baseline. A2E order constant with citation comment. Mirror client loop of `a2f_fork.py:103-131`.
- **Context:** `infra/desktop/tts/a2f_fork.py:103-131`, `infra/desktop/a2f/server.py:9-16,44-51,79-109`, `infra/desktop/a2f/emotion.py:13-16`, `infra/desktop/a2f/arkit.py:13-40`, `infra/desktop/a2f/tests/test_server.py:28-53`, `infra/pi/web/static/js/arkit-map.js:12-36`, `infra/desktop/tts/server.py:157-158,217`
- **Verify:** `infra/pi/agent/.venv/bin/python -m py_compile tools/a2f_probe.py`; `--help` for all three subcommands exits 0; synthetic fixture: write 2 tiny run JSONs (3 frames, known values) in the scratchpad, run `analyze`, assert printed mean/max/std/Δ match hand-computed numbers.

### Task 2: Live capture (production, agent idle)
- **Files:** `docs/research/data/39-a2f-calibration/utterance.wav`, `run-zeros.json`, `run-joy.json`, `run-anger.json` (create)
- **Depends on:** Task 1
- **Scope:** S
- **What:** Execute the live experiment per DD-8: capture the fixed utterance WAV, then three A2F runs with zeros / joy=1.0 / anger=1.0.
- **How:** Confirm agent idle. `ssh -f -N -L 18003:127.0.0.1:8003 Pavel@100.75.88.35`; `curl -s http://127.0.0.1:18003/health` → assert `"backend":"helper"`. `tts` capture → `utterance.wav`. Three `run` invocations (joy = index 1 = 1.0; anger = index 5 = 1.0) → the three JSONs. Kill tunnel (`pkill -f 'ssh -f -N -L 18003'`). First run may take up to 30 s (helper spawn) — not a failure.
- **Context:** Task 1's script `--help`; DD-8 recipe; `.yoke/ai/39-a2f-calibration/39-a2f-calibration-plan.md` connectivity facts.
- **Verify:** all 3 JSONs parse (`python -m json.tool`); frame count ≈ WAV-seconds × 30 (±2) and equal across runs; every `meta` has `backend == "helper"`; identical `meta.wav_sha256` across runs.

### Task 3: Run analysis
- **Files:** scratchpad table output (no repo files)
- **Depends on:** Task 2
- **Scope:** S
- **What:** Produce the quantitative comparison tables.
- **How:** `analyze run-zeros.json run-joy.json run-anger.json --baseline run-zeros.json`; save stdout to the scratchpad for Task 4.
- **Context:** Task 2 artifacts; DD-5, DD-6.
- **Verify:** exit 0; tables contain all 7 groups + Δ and ratio columns; jaw-articulation control roughly stable across runs (flag if not); all numbers finite.

### Task 4: Report + retro link
- **Files:** `docs/research/2026-07-07-a2f-emotion-calibration.md` (create), `docs/retro-epic8-a2f.md` (edit: one-line roadmap step 1 link)
- **Depends on:** Task 3
- **Scope:** M
- **What:** Committed calibration report with pre-registered rule, tables, replay recipe, and the explicit verdict that scopes #40.
- **How:** Sections: context (retro/ADR-0016 gate), environment (backend=helper health snapshot, TTS engine/voice, WAV sha256, utterance text), method incl. **pre-registered rule (DD-6) stated before the numbers**, results tables (Task 3), interpretation (jaw control check; probe-bug rule-out), **replay recipe (DD-7)** with the exact console snippet, **Verdict** section naming exactly one branch ("emotion supply (ADR-0016)" or "SDK tuning first") and the consequence for issue #40. English. Add one line to `docs/retro-epic8-a2f.md` roadmap step 1: link + outcome.
- **Context:** Task 3 tables; DD-6, DD-7; `docs/retro-epic8-a2f.md` (roadmap step 1 wording); `docs/adr/0016-a2f-emotion-supply.md`; `docs/research/2026-07-03-a2f-3d-spike.md` (doc format).
- **Verify:** `grep -n "Verdict" docs/research/2026-07-07-a2f-emotion-calibration.md` non-empty; exactly one branch named; `git status` shows no changes under `infra/`.

### Task 5: Validation
- **Files:** none new
- **Depends on:** all
- **Scope:** S
- **What:** Final validation of the whole change set.
- **How:** `py_compile` on the probe; run existing test suites untouched by the change: `node --test infra/pi/web/static/js/*.test.mjs 2>&1 | tail -5`; confirm diff touches only `tools/`, `docs/`, `.yoke/ai/39-a2f-calibration/`.
- **Context:** all prior tasks.
- **Verify:** all commands exit 0; frontend tests green; `git status` clean after commits.

## Execution

- **Mode:** sub-agents
- **Parallel:** false
- **Reasoning:** Strict chain — T2 gates on the script, T3 on the data, T4 on the numbers; the only shared resource (production A2F helper) serializes behind its lock anyway. T2 is the only live-hardware task.
- **Order:** Task 1 → Task 2 → Task 3 → Task 4 → Task 5

## Verification (from the issue)

- [ ] A repeatable probe script runs one fixed utterance through `:8003` with configurable emotion vectors and records the blendshape streams.
- [ ] A written report (committed) with per-group amplitude/variance deltas for zeros vs `joy=1.0` vs `anger=1.0`.
- [ ] A documented recipe to replay each captured run on `?facedebug=1` for visual comparison.
- [ ] A clear verdict recorded in the report: emotion supply (ADR-0016) vs SDK tuning first.

## Risks

- First helper run after spawn takes up to 30 s — probe timeout covers it; not a failure.
- "Forced joy shows nothing" is a valid result (→ SDK-tuning branch), not a probe bug — rule out probe bugs via the jaw-articulation control (must move in all runs).
- Visual facedebug replay needs a human/browser; the quantitative tables alone carry the verdict — recommend one human visual pass after.
- The `tts` capture triggers one extra A2F run via the production tee — harmless, expected.
