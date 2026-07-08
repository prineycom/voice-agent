# Issue #40 — A2E Emotion Supply: Acceptance Captures vs #39 Baselines

Date: 2026-07-08. Acceptance evidence for issue #40 criteria 1–2: (1) what pure A2E
(audio2emotion) adds over the old all-zeros production baseline, and (2) what the LLM-tag
"preferred emotion" boost adds on top of A2E. Companion to the #39 calibration report
([2026-07-07-a2f-emotion-calibration.md](../../2026-07-07-a2f-emotion-calibration.md)), whose
method, groups and decision-rule style this mirrors. **This document reports; it does not
tune** — all captures are at the shipped knob defaults (Task 8 tunes).

## What changed since #39

In #39 the slim helper ran **no** A2E inference: the WS emotion vector was the entire emotion
input, and production sent all-zeros most of the time. The redeployed helper now runs **A2E on
the utterance audio as the baseline emotion source**; the per-utterance WS emotion vector acts
as an additive "preferred emotion" boost, enabled only when non-zero. Therefore, on the same
probe CLI:

- `--preset zeros` (all-zero vector) ⇒ boost disabled ⇒ **pure A2E baseline** (`a2e-neutral`),
  the new production "neutral tag" case;
- `--preset joy` / `--preset anger` ⇒ **A2E + preferred-emotion boost** (`a2e-joyboost` /
  `a2e-angerboost`), the new production tagged case.

## Environment & provenance

- **A2F:** production helper, redeployed with A2E. Image `voice-agent-a2f:latest`, id
  `6f188ec4afb3` (built 2026-07-08 00:47 CEST), running container `81ac72dd002d`,
  `RestartCount=0`. Health at capture: `backend=helper`, `device=cuda`, `fps=30`. Reached via
  SSH tunnel Pi:18003 → Desktop WSL2:8003.
- **Clock note:** the WSL2 clock was +62 min ahead of the Pi's NTP clock at capture time
  (known WSL skew). Adjusted, the container started ~5 min before the captures
  (`model_loaded=false` at the pre-capture health check); one warm-up run (zeros, discarded)
  was performed first, then all three acceptance runs hit the same warm container instance.
- **Knobs (shipped Dockerfile defaults, verified in the running container's env — no
  overrides):** `A2E_ENABLED=1`, `A2E_EMOTION_STRENGTH=0.6`, `A2E_EMOTION_CONTRAST=1.0`,
  `A2E_LIVE_BLEND_COEF=0.7`, `A2E_LIVE_TRANSITION_TIME=0.5`, `A2E_PREFERRED_STRENGTH=0.5`;
  `A2E_MAX_EMOTIONS` unset ⇒ model default 6 ("keep N largest").
- **Utterance:** the SAME fixed WAV as #39 —
  [`../39-a2f-calibration/utterance.wav`](../39-a2f-calibration/utterance.wav), 7.360 s,
  24 kHz mono PCM16, sha256 `57d42f193ccbcefbf7d145c006ffefd1a532eb299855ba4191692ae5fb72d8e6`
  — so all six runs (three from #39, three from here) are cross-comparable; the analyzer
  enforces the sha match. Every run returned **221 frames** (@30 fps).
- **Data:** this directory — `run-a2e-{neutral,joyboost,angerboost}.json` (each directly
  `window.__a2fInject`-able; replay recipe in the #39 report). Note: the probe hardcodes
  `meta.issue: 39` and titles its analyzer output "issue #39" — cosmetic, ignored.
- **Probe:** [`tools/a2f_probe.py`](../../../../tools/a2f_probe.py), unchanged from #39.

Reproduce:

```bash
ssh -f -N -L 18003:localhost:8003 Pavel@100.75.88.35

# warm-up (discard), then the three acceptance runs
tools/a2f_probe.py run --wav docs/research/data/39-a2f-calibration/utterance.wav \
    --preset zeros --label a2e-neutral    --out docs/research/data/40-a2f-emotion-supply/run-a2e-neutral.json
tools/a2f_probe.py run --wav docs/research/data/39-a2f-calibration/utterance.wav \
    --preset joy   --label a2e-joyboost   --out docs/research/data/40-a2f-emotion-supply/run-a2e-joyboost.json
tools/a2f_probe.py run --wav docs/research/data/39-a2f-calibration/utterance.wav \
    --preset anger --label a2e-angerboost --out docs/research/data/40-a2f-emotion-supply/run-a2e-angerboost.json

# analysis 1: pure A2E vs the old zeros production baseline (#39)
tools/a2f_probe.py analyze docs/research/data/40-a2f-emotion-supply/run-a2e-neutral.json \
    --baseline docs/research/data/39-a2f-calibration/run-zeros.json
# analysis 2: tag boosts vs pure A2E
tools/a2f_probe.py analyze docs/research/data/40-a2f-emotion-supply/run-a2e-*.json \
    --baseline docs/research/data/40-a2f-emotion-supply/run-a2e-neutral.json
```

## Pre-registered acceptance criteria

Stated **before** looking at the results (issue #40 acceptance, criteria 1–2):

> **(a) NEUTRAL — PASS iff** `a2e-neutral` beats the #39 `zeros` baseline by **≥ +0.02
> absolute** group-mean OR **≥ 3×** the baseline group-mean, in **≥ 2** expressive groups
> among **brows / eyes-expressive / mouth-form**.
>
> **(b) JOY BOOST — PASS iff** `a2e-joyboost` amplifies the joy-correlated groups (per #39:
> **brows** and **mouth-form**, where forced joy fired the #39 rule) by **≥ +20%** group-mean
> over `a2e-neutral`.

Additionally recorded (not pass/fail here — inputs to Task 8):
`BrowDownLeft` mean under joyboost (the #39 anomaly, was 0.444; Task 8 target < 0.15);
anger-boost brow-down response; jaw-articulation group vs `a2e-neutral` (Task 8 needs within
±25%); and an explicit check whether the boost lerp-suppresses non-tag dims relative to pure
A2E.

Groups, metrics and aggregates are identical to #39 (`GROUPS` in the probe; per-key mean/max/std
over 221 frames; group = mean-of-means / max-of-maxes / mean-of-stds).

## Results

### Analysis 1 — pure A2E vs the old zeros production baseline

Analyzer output (baseline = #39 `zeros`), verbatim:

#### Runs

| label | emotion vector | frames | backend |
|---|---|---|---|
| zeros | [0, 0, 0, 0, 0, 0, 0, 0, 0, 0] | 221 | helper |
| a2e-neutral | [0, 0, 0, 0, 0, 0, 0, 0, 0, 0] | 221 | helper |

Excluded from grouping: 17 Tongue* keys; 20 other ungrouped keys.

#### brows

| run | mean | max | std | Δmean vs baseline | ratio |
|---|---|---|---|---|---|
| zeros | 0.0188 | 0.0769 | 0.0070 | +0.0000 | 1.0000 |
| a2e-neutral | 0.0341 | 0.2051 | 0.0222 | +0.0153 | 1.8154 |

#### eyes-expressive

| run | mean | max | std | Δmean vs baseline | ratio |
|---|---|---|---|---|---|
| zeros | 0.0001 | 0.0070 | 0.0004 | +0.0000 | 1.0000 |
| a2e-neutral | 0.0031 | 0.0225 | 0.0028 | +0.0030 | 30.4899 |

#### eyes-blink

| run | mean | max | std | Δmean vs baseline | ratio |
|---|---|---|---|---|---|
| zeros | 0.0013 | 0.0068 | 0.0015 | +0.0000 | 1.0000 |
| a2e-neutral | 0.0002 | 0.0025 | 0.0005 | -0.0011 | 0.1558 |

#### eyes-gaze

| run | mean | max | std | Δmean vs baseline | ratio |
|---|---|---|---|---|---|
| zeros | 0.0000 | 0.0000 | 0.0000 | +0.0000 | n/a |
| a2e-neutral | 0.0000 | 0.0000 | 0.0000 | +0.0000 | n/a |

#### mouth-form

| run | mean | max | std | Δmean vs baseline | ratio |
|---|---|---|---|---|---|
| zeros | 0.0511 | 0.4610 | 0.0642 | +0.0000 | 1.0000 |
| a2e-neutral | 0.0490 | 0.4568 | 0.0631 | -0.0021 | 0.9583 |

#### cheeks

| run | mean | max | std | Δmean vs baseline | ratio |
|---|---|---|---|---|---|
| zeros | 0.0125 | 0.0949 | 0.0169 | +0.0000 | 1.0000 |
| a2e-neutral | 0.0246 | 0.0994 | 0.0243 | +0.0121 | 1.9736 |

#### jaw-articulation (control — expected ~stable across runs)

| run | mean | max | std | Δmean vs baseline | ratio |
|---|---|---|---|---|---|
| zeros | 0.0530 | 0.6653 | 0.0574 | +0.0000 | 1.0000 |
| a2e-neutral | 0.0484 | 0.6621 | 0.0556 | -0.0047 | 0.9122 |

### Analysis 2 — tag boosts vs pure A2E

Analyzer output (baseline = `a2e-neutral`), verbatim:

#### Runs

| label | emotion vector | frames | backend |
|---|---|---|---|
| a2e-neutral | [0, 0, 0, 0, 0, 0, 0, 0, 0, 0] | 221 | helper |
| a2e-joyboost | [0, 1, 0, 0, 0, 0, 0, 0, 0, 0] | 221 | helper |
| a2e-angerboost | [0, 0, 0, 0, 0, 1, 0, 0, 0, 0] | 221 | helper |

Excluded from grouping: 17 Tongue* keys; 20 other ungrouped keys.

#### brows

| run | mean | max | std | Δmean vs baseline | ratio |
|---|---|---|---|---|---|
| a2e-neutral | 0.0341 | 0.2051 | 0.0222 | +0.0000 | 1.0000 |
| a2e-joyboost | 0.0457 | 0.1343 | 0.0042 | +0.0116 | 1.3391 |
| a2e-angerboost | 0.0566 | 0.3288 | 0.0108 | +0.0225 | 1.6588 |

#### eyes-expressive

| run | mean | max | std | Δmean vs baseline | ratio |
|---|---|---|---|---|---|
| a2e-neutral | 0.0031 | 0.0225 | 0.0028 | +0.0000 | 1.0000 |
| a2e-joyboost | 0.0007 | 0.0453 | 0.0023 | -0.0024 | 0.2166 |
| a2e-angerboost | 0.0002 | 0.0150 | 0.0005 | -0.0029 | 0.0675 |

#### eyes-blink

| run | mean | max | std | Δmean vs baseline | ratio |
|---|---|---|---|---|---|
| a2e-neutral | 0.0002 | 0.0025 | 0.0005 | +0.0000 | 1.0000 |
| a2e-joyboost | 0.0193 | 0.0305 | 0.0056 | +0.0191 | 99.2729 |
| a2e-angerboost | 0.0034 | 0.0109 | 0.0021 | +0.0032 | 17.6488 |

#### eyes-gaze

| run | mean | max | std | Δmean vs baseline | ratio |
|---|---|---|---|---|---|
| a2e-neutral | 0.0000 | 0.0000 | 0.0000 | +0.0000 | n/a |
| a2e-joyboost | 0.0000 | 0.0000 | 0.0000 | +0.0000 | n/a |
| a2e-angerboost | 0.0000 | 0.0000 | 0.0000 | +0.0000 | n/a |

#### mouth-form

| run | mean | max | std | Δmean vs baseline | ratio |
|---|---|---|---|---|---|
| a2e-neutral | 0.0490 | 0.4568 | 0.0631 | +0.0000 | 1.0000 |
| a2e-joyboost | 0.0670 | 0.5717 | 0.0793 | +0.0180 | 1.3682 |
| a2e-angerboost | 0.0440 | 0.3425 | 0.0516 | -0.0050 | 0.8990 |

#### cheeks

| run | mean | max | std | Δmean vs baseline | ratio |
|---|---|---|---|---|---|
| a2e-neutral | 0.0246 | 0.0994 | 0.0243 | +0.0000 | 1.0000 |
| a2e-joyboost | 0.0170 | 0.1097 | 0.0219 | -0.0075 | 0.6934 |
| a2e-angerboost | 0.0330 | 0.1082 | 0.0266 | +0.0084 | 1.3412 |

#### jaw-articulation (control — expected ~stable across runs)

| run | mean | max | std | Δmean vs baseline | ratio |
|---|---|---|---|---|---|
| a2e-neutral | 0.0484 | 0.6621 | 0.0556 | +0.0000 | 1.0000 |
| a2e-joyboost | 0.0528 | 0.7376 | 0.0607 | +0.0044 | 1.0910 |
| a2e-angerboost | 0.0595 | 0.6321 | 0.0558 | +0.0111 | 1.2290 |

### Per-key means (supplementary, all six runs)

Per-key mean over 221 frames, computed from the run JSONs with the same method as the
analyzer's per-key stats:

| key | 39-zeros | 39-joy | 39-anger | a2e-neutral | a2e-joyboost | a2e-angerboost |
|---|---|---|---|---|---|---|
| BrowDownLeft | 0.0001 | 0.4436 | 0.0608 | 0.0000 | 0.1143 | 0.0152 |
| BrowDownRight | 0.0000 | 0.4430 | 0.0594 | 0.0000 | 0.1141 | 0.0146 |
| BrowInnerUp | 0.0117 | 0.0000 | 0.5088 | 0.1026 | 0.0000 | 0.2531 |
| BrowOuterUpLeft | 0.0408 | 0.0000 | 0.0000 | 0.0338 | 0.0000 | 0.0000 |
| BrowOuterUpRight | 0.0413 | 0.0000 | 0.0000 | 0.0341 | 0.0000 | 0.0000 |
| MouthSmileLeft | 0.0298 | 0.0470 | 0.0318 | 0.0355 | 0.0346 | 0.0307 |
| MouthSmileRight | 0.0299 | 0.0465 | 0.0336 | 0.0361 | 0.0340 | 0.0323 |
| MouthFrownLeft | 0.0738 | 0.1795 | 0.0922 | 0.0628 | 0.0983 | 0.0673 |
| MouthFrownRight | 0.0742 | 0.1868 | 0.0876 | 0.0650 | 0.1009 | 0.0660 |
| EyeWideLeft | 0.0002 | 0.0000 | 0.0000 | 0.0062 | 0.0000 | 0.0001 |
| EyeWideRight | 0.0002 | 0.0000 | 0.0014 | 0.0062 | 0.0000 | 0.0006 |
| EyeSquintLeft | 0.0000 | 0.0567 | 0.0058 | 0.0000 | 0.0024 | 0.0002 |
| EyeSquintRight | 0.0000 | 0.0204 | 0.0065 | 0.0000 | 0.0002 | 0.0000 |
| CheekSquintLeft | 0.0126 | 0.0179 | 0.0482 | 0.0253 | 0.0176 | 0.0326 |
| CheekSquintRight | 0.0123 | 0.0164 | 0.0522 | 0.0239 | 0.0165 | 0.0333 |

## Criteria — PASS/FAIL

### (a) NEUTRAL: **FAIL**

Rule: ≥ 2 of {brows, eyes-expressive, mouth-form} at Δmean ≥ +0.02 abs OR ratio ≥ 3×.

| group | Δmean | ratio | criterion met? |
|---|---|---|---|
| brows | +0.0153 | 1.8154 | no (< +0.02 abs, < 3×) |
| eyes-expressive | +0.0030 | 30.4899 | **yes** (ratio) |
| mouth-form | −0.0021 | 0.9583 | no |

Only 1 of 3 qualifying groups fires ⇒ **FAIL**. Pure A2E at the shipped defaults
(`A2E_EMOTION_STRENGTH=0.6`) adds a real but **subtle** layer over the old flat production:
it is not nothing — per-key, `BrowInnerUp` goes 0.0117 → 0.1026 (+0.091 abs, 8.8×; the
utterance is distressed, so A2E plausibly infers grief/sadness) and cheeks nearly double
(1.97×, a group outside the criterion set) — but the brows *group* mean is diluted by the
other four brow keys and misses both thresholds, and mouth-form is flat. The headline for
Task 8: **A2E alone, untuned, does not clear the pre-registered bar; `A2E_EMOTION_STRENGTH`
is the obvious first knob.**

### (b) JOY BOOST: **PASS**

Rule: joy-correlated groups per #39 (brows, mouth-form) gain ≥ +20% group-mean over
`a2e-neutral`.

| group | joyboost ratio vs a2e-neutral | criterion met? |
|---|---|---|
| brows | 1.3391 (+33.9%) | **yes** |
| mouth-form | 1.3682 (+36.8%) | **yes** |

Both fire ⇒ **PASS**. Also joy-consistent: eyes-blink 99.27× (+0.0191 abs; #39 showed blink
modulation as part of the joy signature). Two honest caveats inside the pass:

- **The "joy" gain is frown/brow-down shaped, not smile-shaped.** Per-key, joyboost leaves
  `MouthSmileLeft/Right` flat (0.0346/0.0340 vs 0.0355/0.0361 neutral) — the mouth-form group
  increase comes from `MouthFrownLeft/Right` (0.0983/0.1009 vs 0.0628/0.0650), and the brows
  increase from `BrowDownLeft/Right`. This is the same counterintuitive joy signature #39
  flagged, now attenuated by the 0.5 preferred-strength blend.
- **The boost is much weaker than #39's forced vector**, as expected from
  `A2E_PREFERRED_STRENGTH=0.5`: brows 0.0457 under joyboost vs 0.1773 under #39 forced joy;
  eyes-expressive 0.0007 vs 0.0193.

### Recorded for Task 8 (no pass/fail here)

- **`BrowDownLeft` mean under joyboost: 0.1143** (was 0.4436 in #39 forced joy; Task 8 target
  < 0.15). The untuned-with-A2E value is **already below the 0.15 target** — the 0.5
  preferred-strength blend alone attenuated the anomaly ~4×. Task 8 should re-check it after
  any strength increase, since raising `A2E_PREFERRED_STRENGTH` will push it back up.
- **Anger-boost brow-down response is weak:** `BrowDownLeft/Right` 0.0152/0.0146 under
  angerboost (vs 0.0608/0.0594 under #39 forced anger). Angerboost's brow response is instead
  `BrowInnerUp`-shaped (0.2531, up from 0.1026 neutral — amplifying the A2E-inferred distress
  rather than adding a distinct angry brow-down).
- **Jaw-articulation vs `a2e-neutral`:** joyboost 1.0910 (+9.1%), angerboost 1.2290 (+22.9%)
  — **both within Task 8's ±25% band**, though angerboost sits near the edge; re-check after
  tuning.

### Suppression check: the boost DOES lerp-suppress non-tag A2E dims — recorded explicitly

Comparing the boosts' NON-tag dims against pure A2E (`a2e-neutral`):

- **eyes-expressive collapses under both boosts:** 0.2166× (joyboost) and 0.0675×
  (angerboost) of the a2e-neutral group mean. Per-key, `EyeWideLeft/Right` go 0.0062 →
  0.0000 under joyboost.
- **A2E-derived brow-raise dims are hard-zeroed:** `BrowInnerUp` 0.1026 → 0.0000 and
  `BrowOuterUpLeft/Right` 0.0338/0.0341 → 0.0000 under joyboost (`BrowOuterUp*` also zeroed
  under angerboost).
- **cheeks drop under joyboost:** 0.6934× (0.0170 vs 0.0246).

The zeroing pattern (several dims to exactly 0.0000, not merely reduced) is consistent with
the preferred-emotion blend re-ranking the emotion vector and the `max_emotions=6` "keep N
largest" truncation then dropping the displaced A2E dims entirely. Consequence: **a non-zero
LLM tag currently replaces part of what A2E inferred from the audio rather than purely adding
to it.** Task 8 owns the decision: tune `A2E_PREFERRED_STRENGTH` (and/or `max_emotions`)
to make the boost additive enough, or take the fallback branch.

## Verdict

**Criterion (a) NEUTRAL: FAIL** — pure A2E at shipped defaults beats the old zeros baseline
in only one of the three qualifying expressive groups (eyes-expressive, 30.5× on a near-zero
base); brows (+0.0153, 1.82×) and mouth-form (0.96×) miss. **Criterion (b) JOY BOOST: PASS**
— brows +33.9% and mouth-form +36.8% over pure A2E, both ≥ +20%.

Net for Task 8 (tuning, not this task): the plumbing works end-to-end and the boost measurably
steers the face, but (1) the A2E baseline is too subtle at `A2E_EMOTION_STRENGTH=0.6`, and
(2) the boost is partially subtractive (non-tag A2E dims lerp-suppressed / truncated to zero).
Both point at the strength/contrast/max_emotions knobs before any fallback is considered.
`BrowDownLeft` under joyboost is already below the 0.15 target (0.1143) and the
jaw-articulation control is within ±25% — record both as the pre-tuning reference points.
