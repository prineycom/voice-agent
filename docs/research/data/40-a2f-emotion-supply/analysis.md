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

---

# Tuning (Task 8) — bounded knob sweep, winning config, bake

Date: 2026-07-08. This section appends to (does not revise) the acceptance report above.
Task 8 tunes the helper's emotion knobs against the pre-registered DONE criteria and bakes
the winner into `infra/desktop/a2f/deploy/Dockerfile`.

## Pre-registered criteria (from the plan, DD-5 — first config satisfying all wins)

> **(a)** neutral-tag pure-A2E run beats the #39 zeros baseline (≥ +0.02 abs or ≥ 3× group
> mean) in ≥ 2 of {brows, eyes-expressive, mouth-form};
> **(b)** joy-boost amplifies joy-correlated groups (brows, mouth-form) ≥ +20% group-mean
> over the SAME config's a2e-neutral run;
> **(c)** `BrowDownLeft` mean under joy-boost < 0.15, while anger's brow response survives
> (> 50% of its value in the untuned-A2E anger run);
> **(d)** jaw-articulation group within ±25% of that config's a2e-neutral.
> Soft goal: prefer configs that keep eyes-expressive ≥ 0.5× under boost, all else equal.

Iteration budget: ≤ 8 container-restart iterations. **Used: 7.**

## Method deltas vs the acceptance captures

Same probe, same fixed WAV (sha-checked), same groups/aggregates, same tunnel. Each
iteration: `docker rm -f` + `docker run … -e KNOB=val … voice-agent-a2f:latest` (image
`6f188ec4afb3` during the sweep), one discarded warm-up run, then zeros + joy + anger
captures (`tune-<config>-{neutral,joyboost,angerboost}.json` in this directory).

**Determinism:** the pipeline is bit-deterministic on a fixed WAV — the iteration-6 no-op
config reproduced the Task 7 runs to four decimals in every group, and the final baked-image
captures reproduced iteration 7 exactly. Differences in the sweep table are therefore real
config effects, not run noise, and the reported margins are trustworthy.

## Sweep table

All unlisted knobs at shipped defaults (`A2E_EMOTION_STRENGTH=0.6`, `A2E_EMOTION_CONTRAST=1.0`,
`A2E_PREFERRED_STRENGTH=0.5`, `A2E_LIVE_BLEND_COEF=0.7`). "supp" = eyes-expressive group
ratio under joyboost vs same-config neutral (soft-goal suppression indicator).

| it | config (overrides) | (a) brows Δ / eyes ratio / mouth ratio | (b) brows / mouth | (c) BDL-joy / anger-brow-vs-0.0566 | (d) jaw joy / anger | supp | verdict |
|---|---|---|---|---|---|---|---|
| — | untuned (Task 7 ref) | +0.0153 / 30.49× / 0.958 | 1.339 / 1.368 | 0.1143 / — (ref) | 1.091 / 1.229 | 0.217 | a ✗, b ✓, c ✓, d ✓ |
| 1 | `STRENGTH=0.8` | **+0.0219** / 46.86× / 0.952 | 1.562 / 1.500 | **0.1590 ✗** / 157% | 1.136 / **1.368 ✗** | 0.518 | a ✓, b ✓, c ✗, d ✗ |
| 2 | `STRENGTH=0.8 PREFERRED=0.3` | +0.0219 / 46.86× / 0.952 | **0.817 ✗** / 1.277 | 0.0774 / 133% | 1.064 / **1.259 ✗** | 0.356 | a ✓, b ✗, c ✓, d ✗ |
| 3 | `STRENGTH=0.8 MAX_EMOTIONS=10` | — no data: **helper hangs** (see finding 1) | — | — | — | — | aborted |
| 4 | `CONTRAST=1.5` | +0.0132 ✗ / 29.20× / 0.945 | 1.453 / 1.382 | 0.1163 / 100% | 1.084 / 1.222 | 0.245 | a ✗, b ✓, c ✓, d ✓ |
| 5 | `A2F_UPPER_FACE_STRENGTH=1.2` | **+0.0310** / 21.17× / 0.954 | **1.044 ✗** / 1.375 | 0.1299 / 142% | 1.092 / 1.228 | 0.539 | a ✓, b ✗, c ✓, d ✓ |
| 6 | `A2F_BS_MULTIPLIERS` (ARKit-case names) | no-op — byte-identical to untuned | | | | | wasted (finding 5) |
| 7 | `A2F_BS_MULTIPLIERS=browInnerUp=1.35,browDownLeft=1.25,browDownRight=1.25` | **+0.0225** / 30.49× / 0.958 | **1.383 / 1.368** | **0.1428** / 134% | 1.091 / 1.229 | 0.217 | **a ✓, b ✓, c ✓, d ✓ — WINNER** |

## Findings that reshaped the search

1. **`A2E_MAX_EMOTIONS` is a trap — and Task 7's truncation hypothesis is wrong.** The A2E
   network classifies only **six** emotions (`angry, disgust, fear, happy, neutral, sad` —
   `/opt/a2f/a2e/network_info.json`), so the model default `max_emotions=6` already truncates
   **nothing**. Worse, any value **> 6 hangs the helper**: the SDK CUDA post-process kernel
   computes `emotionsToZero = inputEmotionsSize - maxEmotions` in unsigned arithmetic
   (`multitrack_postprocess_cuda.cu`), which underflows to ~2^64 and spins the GPU forever —
   verified live (5 consecutive first-frame timeouts at `MAX_EMOTIONS=10`, in-container
   bisect: 6 → first frame in 0.2 s; 8/9/10 → no frame in 90–170 s). The hard-zeroing of
   non-tag dims under boost (BrowInnerUp 0.1026 → 0.0000) is therefore NOT truncation — it is
   the geometry model's nonlinear response to the lerped emotion vector (a joy-dominant
   vector simply produces zero brow-raise), unreachable by these knobs.
2. **Strength scales the boost too.** SDK pipeline order: softmax(contrast·logits) over 6
   dims → nullify `neutral` → keep-N (no-op) → map to the 10-dim a2f space → EMA blend →
   preferred-emotion lerp → transition smoothing → **× strength**. So `STRENGTH=0.8` fixes
   (a) but drags the joy/anger boosts past the (c)/(d) rails; interpolating iterations 1–2,
   (b) needs `PREFERRED ≳ 0.40` while (d) needs `< 0.30` at s=0.8 — provably unsatisfiable.
3. **Contrast backfires on this utterance.** The 6-way softmax argmax is `neutral`, which is
   nullified *after* the softmax — sharpening (contrast 1.5) shrinks all mapped dims
   (neutral brows 0.0341 → 0.0320) instead of amplifying the sadness signal.
4. **Upper-face strength makes (a) and (b) fight.** It amplifies neutral `BrowInnerUp`
   ~5× more than the joy-boost's `BrowDown*` (0.1026→0.1796 vs 0.1143→0.1299 at 1.2), so the
   joy ratio's denominator grows faster than its numerator: (a) passes, (b) brows collapses
   to 1.044. Both criteria are served by the same 5-key brows group.
5. **`A2F_BS_MULTIPLIERS` pose names are the solver's camelCase names** (`browInnerUp`, per
   `bs_skin.npz poseNames`), NOT the ARKit CamelCase the service emits; wrong names warn
   (`unknown pose 'BrowInnerUp' — skipped`) and silently no-op (iteration 6).

## Winner — per-pose brow gains, A2E knobs untouched

`A2F_BS_MULTIPLIERS=browInnerUp=1.35,browDownLeft=1.25,browDownRight=1.25` with every A2E
knob at the shipped defaults. The multipliers act exactly linearly on the solver output
(BrowInnerUp 0.1026 → 0.1385 = ×1.35; BrowDownLeft 0.1143 → 0.1428 = ×1.25), which decouples
the two sides of the brows group: `browInnerUp` (A2E-driven, neutral run) buys (a) without
touching the joy run (where it is zeroed anyway), and `browDown*` (tag-driven, joy run) buys
(b)'s numerator without touching the neutral run (where it is 0.0000). Jaw and eyes are
untouched, preserving (d) and (a)-eyes exactly.

| criterion | rule | winner value | margin | verdict |
|---|---|---|---|---|
| (a) | ≥2 of 3 groups at ≥+0.02 abs or ≥3× | brows **+0.0225** (2.20×); eyes-expressive **30.49×**; mouth-form 0.958× | +0.0025 abs | **PASS** (2/3) |
| (b) | brows & mouth-form ≥ +20% | brows **+38.3%**, mouth-form **+36.8%** | +18.3 / +16.8 pp | **PASS** |
| (c) | BDL-joy < 0.15 AND anger brow > 50% of untuned | **0.1428**; anger brows 0.0758 = **134%** of 0.0566 (BrowInnerUp 0.3417 vs 0.2531) | 0.0072; +84 pp | **PASS** |
| (d) | jaw within ±25% of own neutral | joy **+9.1%**, anger **+22.9%** | 15.9 / 2.1 pp | **PASS** |

**Soft goal NOT met (documented):** eyes-expressive under joyboost is 0.2166× of neutral —
identical to untuned, since the winner leaves the emotion pipeline alone. The only configs
that reached ≥ 0.5× (it 1: 0.518; it 5: 0.539) failed hard criteria, and finding 1 shows the
suppression is a lerp + geometry-nonlinearity property, not reachable by the available knobs.
"All else equal" never held; hard criteria won.

## Bake + confirming captures on the shipped image

The winner is baked as an `ENV A2F_BS_MULTIPLIERS=…` line in
`infra/desktop/a2f/deploy/Dockerfile` (commit `6c7765b`, which also corrects the
`A2E_MAX_EMOTIONS` comment per finding 1). Rebuilt on the Desktop from the pulled repo
(`build_image.sh` via `systemd-run`, image id `5f5fcdda6b2a`), container recreated with **no
`-e` overrides** (`--restart always`, env verified baked in the running container).

Confirming captures — `confirm-baked-{neutral,joyboost,angerboost}.json` — are
**byte-identical in every group statistic to iteration 7** (determinism holds through the
rebuild): (a) brows +0.0225 / eyes 30.49× ✓, (b) 1.3827 / 1.3682 ✓, (c) 0.1428 with anger
`BrowInnerUp` 0.3417 ✓, (d) 1.0910 / 1.2290 ✓. **All four criteria PASS on the production
image.** #39 anomaly re-check: `BrowDownLeft` under joy-boost = 0.1428 < 0.15 target — the
multiplier eats about half the pre-tuning headroom (0.1143 → 0.1428); flagged for any future
`PREFERRED_STRENGTH` increase, which multiplies on top of the baked ×1.25.

## Ops verification (Task 9)

Date: **2026-07-08**. Operational envelope of the final production image: `voice-agent-a2f:latest`,
id **`5f5fcdda6b2a`** (built from commit `6c7765b`), running container `a22c5e6f4ce4`,
`--restart always`, WSL2 Docker on the Desktop, :8003. Measured over SSH from the Pi (probe via
tunnel Pi:18003 → Desktop WSL2:8003), STT (:8001) + TTS (:8002) NSSM services resident and
untouched throughout. Utterance: the same fixed 7.360 s WAV as all other captures here.

### VRAM

Per-process attribution is unavailable on this host (`nvidia-smi --query-compute-apps` reports
`[N/A]` under Windows WDDM and `[Not Found]/[N/A]` inside WSL), so the method is the spike doc's
**delta of total `memory.used`** (WSL `nvidia-smi`), with the a2f container the only thing
exercising the GPU between readings.

| point | state | total used (of 12282 MiB) | delta |
|---|---|---|---|
| (i) | container just restarted, before any utterance (`docker restart`, health ok) | 2022 MiB | — |
| (ii) | after the first utterance (helper spawned, A2F + A2E TRT engines loaded) | 3709 MiB | **+1687 MiB** |
| (iii) | after 5 more utterances (3 steady-state + in-container kill/respawn + 1 confirm) | 3709 MiB | +0 (no growth/leak) |

- **Full helper GPU footprint (A2F + A2E + CUDA context): Δ 1687 MiB ≈ 1.65 GiB.**
- **A2E-attributable delta:** 1687 − 403 (pre-#40 helper figure, ADR-0015) ≈ **1284 MiB ≈ 1.25 GiB**
  — matching the 1.27 GB A2E TRT engine file almost exactly.
- **vs budget:** the new helper total (~1.65 GiB) is ~3.3× the pre-#40 "~0.4–0.5 GB order"
  figure quoted in ADR-0015 / the spike doc — this is the price of A2E and is the new number of
  record. Headroom stays comfortable: 3709/12282 = 30% at capture; even projecting the spike-era
  fully-loaded STT+TTS baseline (5513 MiB), total ≈ 5513 + 1687 = **7200 MiB ≈ 59%** of 12 GB.
- The (i) baseline (2022 MiB) is lower than the spike-era 5513 MiB because the Windows STT/TTS
  services lazy-load their models; both python.exe processes were present. The delta method is
  unaffected — nothing else touched the GPU between readings.

### Latency

Wall-clock from the Pi (`time`, probe over the tunnel; the probe streams PCM without realtime
pacing, so wall time = transport + helper compute, no audio-duration floor):

| measurement | value | budget | verdict |
|---|---|---|---|
| First utterance after container restart (helper spawn + A2F + A2E engine load + full 7.36 s utterance) | **7.28 s** end-to-end | 60 s (`A2F_HELPER_FIRST_TIMEOUT`, baked) | PASS (12% of budget) |
| Steady-state, end-to-end (3 consecutive runs, incl. ~0.5 s python startup + health GET) | **2.01 / 1.60 / 1.63 s** | — | — |
| Steady-state, WS capture only (connect → all 221 frames; transport + helper compute) | **1.70 / 1.32 / 1.49 s** | 5 s (`A2F_HELPER_TIMEOUT`, in-flight, helper-side) | PASS |

The 5 s per-utterance timeout wraps only the server-side helper I/O (`engine.py
_utterance_io`), a strict subset of the capture time — so helper compute is **< 1.7 s** for a
7.36 s utterance (≥ 4.3× realtime), comfortably under the plan's 2.5 s helper-side bar. **No
`A2F_HELPER_TIMEOUT` change needed; the Dockerfile is untouched by this task.** Honest caveat:
per-utterance compute is not logged service-side, so the 1.32–1.70 s figures include tunnel
transport (~0.35 MB PCM up, ~1 MB frames down) and are an upper bound on helper compute.

### Supervision

| check | result |
|---|---|
| `docker restart voice-agent-a2f` → health | PASS — `{"status":"ok",…}` at the first poll, ≤ 8 s after the restart command (incl. SSH overhead) |
| `RestartPolicy` | PASS — `always` (`docker inspect`) |
| Boot Scheduled Task | PASS — `schtasks /query /tn voice-agent-a2f-boot` → `Ready` |
| In-container helper kill (`docker exec … pkill -f a2f_stream`) | PASS — next probe run succeeded **transparently** (no failed or degraded run): engine.py detected the dead child and respawned via the first-timeout path, 7.42 s (≈ cold figure); the run after that was back to steady 2.08 s; VRAM returned to 3709 MiB (no leak); `RestartCount` stayed 0 — recovery is engine.py's crash→respawn, Docker never intervened |

Kill-test caveat: the helper was killed while **idle**. Per engine.py, a mid-utterance crash
would instead fail that one utterance (WS error to the client) and respawn on the next call —
the tested path confirms the respawn machinery; the single-utterance-loss path is by design.

Note: the health endpoint's `model_loaded` is hardcoded `false` for the helper backend
(server.py:73, "reports real load once wired") — it is NOT an indicator of engine load; use the
first-utterance latency signature instead.
