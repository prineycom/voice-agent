# Wake-word gate — model + training + test harness (#57)

The **Wake-word gate** classifier for the agent's Dormant→Active activation
(Epic #56, ADR-0021). It scores the user's incoming audio server-side on the Pi
and, on a hit for one of the three wake words — **`Приней`, `Приня`, `хей джарвис`**
— flips the agent from Dormant to Active. This directory holds the model
artifacts, the training config to (re)build them, and a standalone test harness
that proves detection off the agent.

Uses [`livekit-wakeword`](https://github.com/livekit/livekit-wakeword) (openWakeWord
lineage): a bundled mel-spectrogram + Google speech-embedding front-end feeds a
small wake-word classifier ONNX. Only the classifier is per-keyword; the front-end
is shared, so adding keywords is nearly free at inference time.

```
wakeword/
├── models/
│   ├── hey_jarvis.onnx      # pretrained (openWakeWord) — WORKING out-of-box model for «хей джарвис»
│   ├── prinei.onnx          # (not committed yet) trained «Приней/Приня/хей джарвис» — see "Retrain"
│   └── thresholds.json      # per-model activation thresholds the Pi gate (#58) loads
├── configs/
│   └── prinei-prod.yaml     # livekit-wakeword training config (VoxCPM2, all three phrases)
├── harness/
│   ├── detect.py            # stream WAV clips → per-clip peak score + hit/miss + self-check
│   └── make_test_clips.py   # synthesize test clips via the Desktop TTS (dev helper)
└── clips/                   # committed test clips (neg_* must-not-fire, ref_* informational)
```

## Status: what works today vs what needs training

| Wake word     | Model                                   | State                                             |
| ------------- | --------------------------------------- | ------------------------------------------------- |
| `хей джарвис` | `models/hey_jarvis.onnx` (pretrained)   | **Working out-of-box** — used by the Pi gate now  |
| `Приней`      | `models/prinei.onnx` (to be trained)    | Config ready; needs a Desktop VoxCPM2 training run |
| `Приня`       | (same combined model)                   | Config ready; trained together with `Приней`      |

Per the epic, the pipeline is built and tested first against the **out-of-box**
`hey_jarvis` model (openWakeWord's pretrained "hey jarvis", which `livekit-wakeword`
loads directly — it is backward compatible). The custom short Russian names are
trained afterwards on the Desktop GPU. The Pi gate (#58) loads a comma-separated
list of models, so `prinei.onnx` drops in next to `hey_jarvis.onnx` with no code
change once trained.

## Test harness

Inference-only, needs just `livekit-wakeword` (numpy + onnxruntime — already in the
Pi agent venv). Run it with that interpreter:

```bash
AGENT=../../pi/agent/.venv/bin/python

# Score the committed clips against the out-of-box model (self-checks neg_* clips)
$AGENT harness/detect.py -m models/hey_jarvis.onnx -t 0.5 --clips clips

# Multi-keyword gate (as the Pi runs it), against your own recordings
$AGENT harness/detect.py -m models/hey_jarvis.onnx models/prinei.onnx -t 0.5 my_clip.wav
```

`detect.py` slides a 2 s window (320 ms stride) over each clip and prints each
model's **peak** score. Clips named `pos_*` are asserted to fire, `neg_*` to stay
silent, and the process exits non-zero on any mismatch — a self-checking
regression. `ref_*` clips are informational only.

`make_test_clips.py` synthesizes stand-in clips through the Desktop CustomVoice
TTS (`ws://100.75.88.35:8002/tts`) into `clips/`. **These validate the pipeline and
the false-positive profile, not real-user recall** (see below).

## Thresholds

`models/thresholds.json` holds the per-model activation cutoff the Pi gate loads
(default **0.5**, the openWakeWord standard). How it was chosen and what was
observed on this hardware:

- **Negatives are clean.** Every general-Russian test clip (`neg_*`), including the
  dangerous near-misses «принеси», «привет», «джаз», scores **≈ 0.000** against
  `hey_jarvis`. The false-accept risk at 0.5 is effectively nil — this satisfies
  the "general Russian conversation does not trip the detector" acceptance
  criterion, and is the reproducible part of the harness (`detect.py --clips clips`).
- **Positives from TTS are unreliable as a signal.** The Desktop TTS is a *Russian*
  CustomVoice speaker and is **non-deterministic** (see repo memory
  `desktop-tts-qwen-sampling-seed`). Its rendering of the English-trained
  «хей джарвис» swings run-to-run between ~0.0 and ~0.42 for the *same text*. So a
  synthetic positive proves the model *can* fire but is not a dependable recall
  measurement. A contiguous «Хей Джарвис» (no comma/pause) scores far higher than
  a comma'd one — a pause pushes the two words outside the 2 s window.
- **Recommendation:** keep 0.5 as the default. If real-user testing shows the short
  names miss too often, lower the trained model's entry in `thresholds.json` toward
  0.3–0.4 and re-check `neg_*` stays silent at that cutoff. The trained
  `conv_attention` model's own eval reports an optimal threshold (often ~0.5–0.7);
  prefer that once `livekit-wakeword eval` has run.
- **Confirmed on live prod (2026-07-13):** with the out-of-box `hey_jarvis` model at
  a lowered `WAKEWORD_THRESHOLD=0.4`, a real user wakes the agent reliably when
  «хей джарвис» is pronounced the **English** way ("hey JAR-vis") — a heavily
  Russian-accented «хей» (velar Х) does not fire. This is the expected out-of-box
  gap; the custom VoxCPM2 model (trained on Russian «Приней/Приня/хей джарвис») is
  what makes natural Russian pronunciation work. Set `WAKEWORD_DEBUG=1` to log live
  scores while tuning the threshold.

### Testing positives (the human-in-the-loop step)

Reliable positive acceptance ("wakes on all three phrases spoken by the primary
user") requires the **primary user's own recorded clips** — TTS cannot stand in for
it. To do that acceptance run:

```bash
# record 16 kHz mono WAVs named pos_*.wav (arecord -r 16000 -c1 -f S16_LE pos_prinei.wav)
$AGENT harness/detect.py -m models/hey_jarvis.onnx models/prinei.onnx -t 0.5 pos_*.wav
```

## Inference cost (measured on the Pi 5, `onnxruntime` CPU)

- **~75 ms per 2 s-window `predict()`** with one model; **~83 ms with three** (the
  mel + embedding front-end dominates and is computed once, so extra keywords add
  ~4 ms each). RTF ≈ 0.04.
- The gate scores on a stride, not every frame: at a ~2 Hz cadence (predict every
  ~500 ms) that's **< 20 % of one of the Pi's 4 cores**, continuously — cheap
  enough for an always-on gate in front of the heavy Desktop GPU STT.

## Retrain recipe (custom `Приней/Приня` model)

Training is GPU + dataset work on the **Desktop** (RTX 4070), **not** the Pi. It
uses `livekit-wakeword`'s VoxCPM2 synthetic-data backend — VoxCPM2 is already stood
up on the Desktop (repo memory `desktop-tts-voxcpm2-prod`, ADR-0018); point
`voxcpm_tts.local_model_path` in the config at that snapshot to skip re-downloading
weights.

```bash
# On the Desktop, in a livekit-wakeword checkout (separate env from the Pi agent):
git clone https://github.com/livekit/livekit-wakeword && cd livekit-wakeword
uv sync --extra train --extra voxcpm            # torch + VoxCPM2 deps (GPU)

# Copy this repo's config in, then:
livekit-wakeword setup --config prinei-prod.yaml   # fetch backgrounds/RIRs + VoxCPM2
livekit-wakeword run   prinei-prod.yaml            # generate → augment → train → export
livekit-wakeword eval  prinei-prod.yaml            # DET curve, AUT, FPPH, optimal threshold

# Export lands at output/prinei_ru/prinei_ru.onnx — commit it here:
cp output/prinei_ru/prinei_ru.onnx <voice-agent>/infra/desktop/wakeword/models/prinei.onnx
```

Config knobs that matter for the short Russian names (all in
`configs/prinei-prod.yaml`, with rationale inline):

- `target_phrases` — all three wake words in one combined model.
- `custom_negative_phrases` — Russian false-friends («принеси», «привет», «джаз»,
  bare «Джарвис» …); the auto adversarial negatives are English-biased.
- `n_samples` / `voice_design_prompts` — raise both if multilingual recall is weak
  (the documented VoxCPM lever; short non-English names are the hard case).
- `model.model_type: conv_attention` — the low-false-positive head (100× fewer FPPH
  than the openWakeWord DNN in the vendor's own benchmark).

After training, re-run the harness on real recordings and, if needed, tune
`thresholds.json`.

### Training feasibility snapshot (why it's a scheduled step, not automated here)

Probed on the Desktop during the epic build: the RTX 4070 (12 GB) had only
**~4.3 GB free** — the live STT + TTS + A2F prod services hold ~7.6 GB — and no
`uv` / VoxCPM2 / `livekit-wakeword[train]` env was set up (base Python 3.10). A
full run (50k+ VoxCPM syntheses → 100k training steps) is hours of GPU work that
would contend with and degrade the live voice agent, so it must be **scheduled**
(free the GPU or run off-hours), not fired off alongside production. Combined with
the positive-recall caveat above (needs the primary user's recorded voice), custom
`Приней`/`Приня` training is an intentional human/GPU step. The software pipeline
(#58–#60) is complete and runs today on the out-of-box `hey_jarvis` model.
