# Issue #39 — A2F Emotion-Vector Calibration (forced-emotion vs zeros A/B)

Date: 2026-07-07. Goal: run the gate experiment from the Epic 8 retrospective roadmap (step 1)
that gates ADR-0016. See [retro](../retro-epic8-a2f.md), [ADR-0016](../adr/0016-a2f-emotion-supply.md),
issue #39.

## Context

The retro established that the shipped A2F face is visually indistinguishable from volume
lip-sync, and that the emotion input to A2F is zero most of the time (`neutral`/missing tag →
all-zeros A2E vector, no A2E audio inference in the slim helper). Before investing in emotion
supply (ADR-0016, roadmap step 2), one question must be answered: **does the A2F emotion vector
actually buy facial expressiveness?** I.e., separate "the emotion input is zero" from "A2F
output is inherently flat regardless of input". ADR-0016 explicitly gates on this: if even a
forced full-strength emotion barely moves the raw face, the investment redirects to SDK
tuning/model configuration first.

## Environment

- **A2F:** the production helper (ADR-0015). Health snapshot at capture time: `backend=helper`,
  `device=cuda`, `fps=30`, image `voice-agent-a2f:latest`. Reached via an SSH tunnel
  Pi:18003 → Desktop WSL2:8003 (the container's :8003 is not directly reachable over Tailscale).
- **TTS:** production service, engine `voice_clone`, voice `default`, `ws://100.75.88.35:8002/tts`.
- **Utterance (fixed across all runs):** "No — no, this can't be happening. After everything we
  built together, you're telling me it's all gone? That is absolutely unbelievable." — 7.360 s,
  24 kHz mono PCM16, sha256 `57d42f193ccbcefbf7d145c006ffefd1a532eb299855ba4191692ae5fb72d8e6`.
  Captured 2026-07-07. Every run returned **221 frames** (@30 fps).
- **Data:** [`docs/research/data/39-a2f-calibration/`](data/39-a2f-calibration/) —
  `utterance.wav` + `run-{zeros,joy,anger}.json`. Note: `utterance.wav` is force-added past the
  repo's `*.wav` gitignore rule.
- **Probe:** [`tools/a2f_probe.py`](../../tools/a2f_probe.py) (subcommands `tts` / `run` /
  `analyze`; see its docstring).
- **Emotion vectors:** A2E model order is
  `[grief, joy, disgust, outofbreath, pain, anger, amazement, cheekiness, sadness, fear]`.
  Runs: **zeros** (all 0), **joy** (index 1 = 1.0), **anger** (index 5 = 1.0).

Reproduce:

```bash
# SSH tunnel to the A2F helper (direct :8003 is unreachable over Tailscale)
ssh -N -L 18003:localhost:8003 Pavel@100.75.88.35 &

# Capture the fixed utterance from production TTS
tools/a2f_probe.py tts --out docs/research/data/39-a2f-calibration/utterance.wav

# Three runs: zeros baseline + two forced full-strength emotions
tools/a2f_probe.py run --wav docs/research/data/39-a2f-calibration/utterance.wav \
    --preset zeros --out docs/research/data/39-a2f-calibration/run-zeros.json
tools/a2f_probe.py run --wav docs/research/data/39-a2f-calibration/utterance.wav \
    --preset joy   --out docs/research/data/39-a2f-calibration/run-joy.json
tools/a2f_probe.py run --wav docs/research/data/39-a2f-calibration/utterance.wav \
    --preset anger --out docs/research/data/39-a2f-calibration/run-anger.json

# Grouped comparison against the zeros baseline
tools/a2f_probe.py analyze docs/research/data/39-a2f-calibration/run-*.json \
    --baseline docs/research/data/39-a2f-calibration/run-zeros.json
```

## Method & pre-registered decision rule

Stated **before** looking at the results:

> **Verdict = "emotion supply (ADR-0016) proceeds"** iff joy or anger produces, in at least one
> expressive group (**brows**, **mouth-form**, **cheeks**, **eyes-expressive**), a group-mean
> amplitude increase of **≥ 0.05 absolute** OR **≥ 3× the zeros baseline**. Otherwise the
> verdict is **"SDK tuning first"** (ADR-0016's fallback branch).

Grouping mirrors how the frontend consumes the blendshape keys
(`infra/pi/web/static/js/arkit-map.js`), with eyes split into expressive / blink / gaze, plus a
**jaw-articulation control group** — driven by the audio itself, expected ~stable across
emotion vectors. Metrics: per-key mean/max/std over the 221 frames; group aggregates =
mean-of-means / max-of-maxes / mean-of-stds.

## Results

Analyzer output (`a2f_probe.py analyze`, baseline = zeros), verbatim:

### Runs

| label | emotion vector | frames | backend |
|---|---|---|---|
| zeros | [0, 0, 0, 0, 0, 0, 0, 0, 0, 0] | 221 | helper |
| joy | [0, 1, 0, 0, 0, 0, 0, 0, 0, 0] | 221 | helper |
| anger | [0, 0, 0, 0, 0, 1, 0, 0, 0, 0] | 221 | helper |

Excluded from grouping: 17 Tongue* keys; 20 other ungrouped keys.

### brows

| run | mean | max | std | Δmean vs baseline | ratio |
|---|---|---|---|---|---|
| zeros | 0.0188 | 0.0769 | 0.0070 | +0.0000 | 1.0000 |
| joy | 0.1773 | 0.4844 | 0.0089 | +0.1585 | 9.4392 |
| anger | 0.1258 | 0.5605 | 0.0043 | +0.1070 | 6.6958 |

### eyes-expressive

| run | mean | max | std | Δmean vs baseline | ratio |
|---|---|---|---|---|---|
| zeros | 0.0001 | 0.0070 | 0.0004 | +0.0000 | 1.0000 |
| joy | 0.0193 | 0.1526 | 0.0205 | +0.0192 | 189.5884 |
| anger | 0.0034 | 0.0584 | 0.0057 | +0.0033 | 33.4957 |

### eyes-blink

| run | mean | max | std | Δmean vs baseline | ratio |
|---|---|---|---|---|---|
| zeros | 0.0013 | 0.0068 | 0.0015 | +0.0000 | 1.0000 |
| joy | 0.0714 | 0.0965 | 0.0103 | +0.0702 | 57.1229 |
| anger | 0.0166 | 0.0264 | 0.0034 | +0.0154 | 13.2995 |

### eyes-gaze

| run | mean | max | std | Δmean vs baseline | ratio |
|---|---|---|---|---|---|
| zeros | 0.0000 | 0.0000 | 0.0000 | +0.0000 | n/a |
| joy | 0.0000 | 0.0000 | 0.0000 | +0.0000 | n/a |
| anger | 0.0000 | 0.0000 | 0.0000 | +0.0000 | n/a |

### mouth-form

| run | mean | max | std | Δmean vs baseline | ratio |
|---|---|---|---|---|---|
| zeros | 0.0511 | 0.4610 | 0.0642 | +0.0000 | 1.0000 |
| joy | 0.1169 | 0.8664 | 0.1172 | +0.0657 | 2.2858 |
| anger | 0.0503 | 0.2713 | 0.0422 | -0.0008 | 0.9835 |

### cheeks

| run | mean | max | std | Δmean vs baseline | ratio |
|---|---|---|---|---|---|
| zeros | 0.0125 | 0.0949 | 0.0169 | +0.0000 | 1.0000 |
| joy | 0.0171 | 0.1212 | 0.0264 | +0.0047 | 1.3761 |
| anger | 0.0502 | 0.1258 | 0.0287 | +0.0377 | 4.0309 |

### jaw-articulation (control — expected ~stable across runs)

| run | mean | max | std | Δmean vs baseline | ratio |
|---|---|---|---|---|---|
| zeros | 0.0530 | 0.6653 | 0.0574 | +0.0000 | 1.0000 |
| joy | 0.0796 | 0.9593 | 0.0795 | +0.0266 | 1.5012 |
| anger | 0.0780 | 0.5793 | 0.0543 | +0.0249 | 1.4701 |

## Interpretation

- **The decision rule fires decisively, on multiple groups.** Brows: joy Δmean **+0.1585** at
  **9.44×**, anger Δmean **+0.1070** at **6.70×** — both criteria satisfied at once.
  Mouth-form: joy Δmean **+0.0657** (absolute criterion). Eyes-expressive: joy **189.6×**,
  anger **33.5×** (ratio criterion; tiny absolute amplitudes on a near-zero baseline).
  Cheeks: anger **4.03×** (ratio criterion).
- **The emotion axes differentiate — this is not a global gain knob.** Joy produces a smile
  (mouth-form 2.29×) plus wide/squint eye activity and blink modulation; anger drives cheeks
  (4.03×) and brows while leaving mouth-form untouched (0.98×). The model responds with
  emotion-specific facial patterns.
- **Control caveat (flagged honestly): jaw-articulation is NOT stable.** Joy +50.1% and anger
  +47.0% vs the zeros mean (0.0796 / 0.0780 vs 0.0530); joy max 0.9593 vs 0.6653. The pattern
  argues against a capture artifact: all runs have identical frame counts, the same backend and
  the same WAV sha, and anger's mouth-form at 0.98× would be impossible if the runs were
  globally incomparable. The plausible reading is that emotion legitimately co-modulates
  articulation amplitude — emotions widen jaw movement. Consequence: jaw is an imperfect
  control, but the runs remain comparable.
- **Anomaly worth noting:** joy drives mean `BrowDownLeft` ≈ **0.444** (vs ~0.000 under zeros).
  A strong brow-*down* under joy is counterintuitive; flagged for the #40 tuning pass
  (per-blendshape multipliers) to examine.
- **eyes-gaze is identically zero in all runs** — A2F never drives gaze. Gaze stays a frontend
  concern.
- **The zeros baseline is not static:** it shows full lip-sync articulation (per-key max 0.815
  across all blendshapes; jaw-articulation group max 0.6653) with near-zero brows/eyes — this is
  precisely the shipped "just lip-sync" look described in the retro, now quantified.

## Replay recipe (visual verification)

To replay any run on the raw-ARKit debug face: open the frontend with `?facedebug=1` (motions
frozen), agent idle, then paste into the DevTools console:

```js
const inp = document.createElement('input'); inp.type = 'file';
inp.onchange = async () => {
  const run = JSON.parse(await inp.files[0].text());
  run.frames.forEach(f => window.__a2fInject(f));
  window.__a2fInject({ done: true });
};
inp.click();
```

Pick a run JSON from `docs/research/data/39-a2f-calibration/`. The frames route through the
playback scheduler and play at real speed (~7.4 s); re-picking another file re-anchors
automatically; per-stream stats are in `window.__a2fStats`. To compare zeros vs joy
side-by-side, replay them sequentially.

## Verdict

**Emotion supply (ADR-0016) proceeds.**

The emotion input is the bottleneck, not the model: forced full-strength vectors produce large,
emotion-specific facial responses on the very same audio. The gated decision in ADR-0016 is
confirmed on its "proceed" branch; the SDK-tuning-first fallback branch is **not** taken.
Consequence for issue #40: proceed as written — A2E audio inference in the helper + LLM tag as
an additive boost + SDK knob tuning while in there.

Two notes within the verdict:

- Absolute amplitudes even under forced emotion remain modest in some groups (eyes-expressive
  mean 0.019) — the Live2D amplification step (issue #41, retro roadmap step 3) stays necessary.
- The `BrowDownLeft`-under-joy anomaly goes onto #40's tuning checklist (per-blendshape
  multipliers).
