# Report: 39-a2f-calibration

**Plan:** .yoke/ai/39-a2f-calibration/39-a2f-calibration-plan.md
**Mode:** sub-agents
**Status:** ✅ complete

## Tasks

| #   | Task                                | Status  | Commit    | Concerns |
| --- | ----------------------------------- | ------- | --------- | -------- |
| 1   | Author `tools/a2f_probe.py`         | ✅ DONE | `59b9c50` | —        |
| 2   | Live capture (production, idle)     | ✅ DONE | `bdf90ce` | —        |
| 3   | Run analysis                        | ✅ DONE | —         | —        |
| 4   | Report + retro link                 | ✅ DONE | `ab480fb` | —        |
| 5   | Validation                          | ✅ DONE | —         | —        |

## Post-implementation

| Step          | Status     | Commit |
| ------------- | ---------- | ------ |
| Validate      | ✅ pass    | —      |
| Documentation | ⏭️ skipped | —      |
| Format        | ✅ done (no formatter configured) | — |

## Outcome

**Verdict: Emotion supply (ADR-0016) proceeds.** Forced emotion vectors produce large, emotion-specific facial responses on the same audio — the bottleneck is the emotion input (all-zeros in production), not the A2F model. The SDK-tuning-first fallback branch is NOT taken. Issue #40 proceeds as written.

Key numbers (group mean vs zeros baseline):
- brows: joy Δ+0.1585 (9.44×), anger Δ+0.1070 (6.70×) — fires the pre-registered rule on both conditions
- mouth-form: joy Δ+0.0657 (2.29×); anger unchanged (0.98×) — emotion-specific patterns, not global gain
- eyes-expressive: joy 189.6×, anger 33.5× (near-zero baseline)
- cheeks: anger 4.03×
- eyes-gaze: identically zero in all runs — A2F never drives gaze

Caveats recorded in the report: jaw-articulation control not stable (+50%/+47% — emotion co-modulates articulation; runs remain comparable), BrowDownLeft≈0.444 under joy (anomaly, flagged for #40's tuning checklist), absolute eye amplitudes remain modest → #41 (Live2D amplification) stays necessary.

## Review results

- Task 1 review: ✅ approved. 3 minor notes recorded: mid-stream `WebSocketException` not caught (raw traceback instead of clean abort), `fps_nominal` hardcoded 30 (could read `health.fps`), 60 s is whole-capture not per-recv timeout.
- Task 2 review: ✅ approved. Notes: capture URL is the SSH tunnel (provenance fine, health confirms real helper); `health.model_loaded:false` looks like a stale helper health field.
- Task 4 review: ✅ approved. 1 minor note: the report's sha256 is the raw-PCM hash (as printed by the probe), not `sha256sum utterance.wav` — could be annotated.
- `.gitignore` `*.wav` rule required `git add -f` for `utterance.wav`; any future re-capture needs the same.

## Validation

infra/pi/agent/.venv/bin/python -m py_compile tools/a2f_probe.py ✅
node --test infra/pi/web/static/js/*.test.mjs ✅ (all passed, 0 failed)
git diff scope check (only tools/, docs/, .yoke/) ✅
git status clean ✅

## Changes summary

| File | Action | Description |
| ---- | ------ | ----------- |
| tools/a2f_probe.py | created | calibration probe: tts / run / analyze subcommands, zero new deps |
| docs/research/data/39-a2f-calibration/utterance.wav | created | fixed utterance, 7.360 s, voice_clone "default" |
| docs/research/data/39-a2f-calibration/run-{zeros,joy,anger}.json | created | captured blendshape streams, 221 frames each, backend=helper |
| docs/research/2026-07-07-a2f-emotion-calibration.md | created | calibration report: pre-registered rule, tables, replay recipe, verdict |
| docs/retro-epic8-a2f.md | modified | roadmap step 1 marked Done with link |

## Commits

- `baf7286` #39 docs(39-a2f-calibration): add implementation plan
- `59b9c50` #39 feat(39-a2f-calibration): a2f emotion-vector probe (tts/run/analyze)
- `bdf90ce` #39 feat(39-a2f-calibration): captured calibration runs (zeros/joy/anger) + fixed utterance
- `ab480fb` #39 docs(39-a2f-calibration): calibration report + verdict (emotion supply proceeds)
