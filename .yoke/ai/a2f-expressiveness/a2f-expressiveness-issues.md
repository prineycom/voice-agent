# A2F expressiveness — issue index

Source: `docs/retro-epic8-a2f.md` roadmap (Epic 8 retrospective, 2026-07-06) + ADR-0016.
Published 2026-07-06.

| # | Title | Type | Blocked by | URL |
|---|-------|------|------------|-----|
| 39 | A2F calibration: forced-emotion vs zero-vector A/B on the raw blendshape stream | AFK | — | https://github.com/prineycom/voice-agent/issues/39 |
| 40 | Emotion supply: A2E audio inference in the A2F helper + emotion-tag boost + SDK tuning (ADR-0016) | AFK | #39 | https://github.com/prineycom/voice-agent/issues/40 |
| 41 | Live2D amplification: expander curves + discrete vtuber accents in the ARKit mapping | AFK | #40 | https://github.com/prineycom/voice-agent/issues/41 |
| 42 | Prosody-driven head-sway and nods layered over idle motions | AFK | — (priority after #40/#41) | https://github.com/prineycom/voice-agent/issues/42 |
| 43 | Gate: 3D web avatar spike (three.js + VRM, full 52 blendshapes) — only if Live2D ceiling proves too low | HITL | #40, #41 + human gate verdict | https://github.com/prineycom/voice-agent/issues/43 |

Labels: #39–#42 `ready-for-agent`, #43 `ready-for-human`. All typed `Task`. No parent issue (roadmap follows Epic #33, closed scope).


---

# Issue #39 body

## What to build

**The gate experiment for everything in the Epic 8 retro roadmap** (`docs/retro-epic8-a2f.md`, Roadmap step 1). It answers one question: *how much facial expressiveness does the A2F emotion vector actually buy us?* — separating "emotion input is zero" from "the A2F model's output is inherently flat".

### Background (root-cause chain from the retro)

Epic 8 shipped A2F facial animation whose emotion input is effectively disconnected: the only emotion source is the LLM inline tag (5-value enum), and `neutral`/absent — the common case — maps to an **all-zeros** 10-dim A2E vector (`infra/desktop/a2f/emotion.py`). Audio2Emotion inference from audio prosody is not run by our slim helper at all (`infra/desktop/a2f/a2f_stream/main.cpp` feeds the emotion accumulator only from the tag; SDK executors use default parameters). With a zero vector A2F degenerates to near-pure phoneme articulation — visually "just lip-sync". Confirmed observation: the raw-ARKit `?facedebug=1` face is flat too, so the data itself is poor — the bottleneck is upstream of the Live2D mapping.

### The experiment

Run the **same utterance** (one fixed WAV / TTS reply, ~5–10 s, emotionally chargeable text) through the production A2F service (`ws://<desktop>:8003`, protocol: JSON `{"emotion": ...}` → PCM frames → `{"end"}`; see `infra/desktop/a2f/server.py` docstring) with at least these emotion vectors:

- all zeros (today's `neutral` baseline)
- forced `joy = 1.0`
- forced `anger = 1.0` (second axis, cross-check)

Capture the emitted blendshape frame streams and produce a **quantitative comparison report**: per-blendshape-group (brows, eyes/lids, mouth-form, cheeks — grouping as in `infra/pi/web/static/js/arkit-map.js`) mean/max amplitude and variance deltas between runs. Also capture the recipe to replay each run visually on the browser debug face (`?facedebug=1` + `window.__a2fInject`, see `infra/pi/web/static/js/facedebug.js` / `blendshapes.js`) so a human can eyeball the difference.

The A2F service is production (ADR-0015) — **read-only usage, no server changes** in this issue. A throwaway probe script may live in `tools/` or the issue's `.yoke/ai/` artifacts.

### Outcome interpretation (drives the roadmap)

- **Forced emotion produces clearly richer motion** → invest in the emotion supply chain (A2E + tag boost, ADR-0016) — unblocks the next issue.
- **Even forced full-strength emotion is flat** → redirect investment to helper SDK tuning / model configuration first (emotion strength, face params, per-blendshape multipliers — currently library defaults); the emotion-supply issue's scope shifts accordingly.

## Acceptance criteria

- [ ] A repeatable probe script runs one fixed utterance through `:8003` with configurable emotion vectors and records the blendshape streams.
- [ ] A written report (committed, e.g. `docs/` or the issue thread) with per-group amplitude/variance deltas for zeros vs `joy=1.0` vs `anger=1.0`.
- [ ] A documented recipe to replay each captured run on `?facedebug=1` for visual comparison.
- [ ] A clear verdict recorded in the report: which of the two roadmap branches applies (emotion supply vs SDK tuning first).

## References

- `docs/retro-epic8-a2f.md` — retro, root-cause chain, roadmap (this is step 1).
- `docs/adr/0016-a2f-emotion-supply.md` — the decision this experiment gates.
- `docs/adr/0015-a2f-helper-production.md` — the production helper this probes.
- Code: `infra/desktop/a2f/emotion.py` (enum→vector), `infra/desktop/a2f/server.py` (WS protocol), `infra/desktop/a2f/a2f_stream/main.cpp` (helper, default SDK params), `infra/pi/web/static/js/facedebug.js` + `blendshapes.js` (`window.__a2fInject`).
- Glossary: `.yoke/context.md` — *Emotion vector*, *Audio2Emotion*, *Blendshape*.

## Blocked by

None - can start immediately

---

# Issue #40 body

## What to build

**Implement ADR-0016** (`docs/adr/0016-a2f-emotion-supply.md`): make Audio2Emotion (A2E) audio inference the baseline emotion source for A2F facial animation, with the LLM emotion tag as an additive boost — and tune the helper's SDK knobs while in there. This is Roadmap step 2 of `docs/retro-epic8-a2f.md`.

### Background

Today the A2F emotion vector comes **only** from the LLM inline tag (5-value enum → sparse 10-dim vector, `infra/desktop/a2f/emotion.py`); `neutral`/absent yields all zeros, so most speech renders with near-pure phoneme articulation ("just lip-sync"). The full NVIDIA A2F NIM always blends in A2E — emotion inferred from the audio's prosody — but our slim helper (`infra/desktop/a2f/a2f_stream/main.cpp`, ADR-0015) skips it: it feeds the `EmotionAccumulator` a manual vector and creates all executors with default SDK parameters.

### The change (Desktop side)

1. **A2E in the helper**: run the A2E network on the same PCM already fed to A2F, so the emotion vector continuously follows the voice's prosody — no new services, no LLM involvement. (The A2X SDK ships audio2emotion executors; the helper already links `audio2x`.)
2. **Tag as boost, not source**: combine the tag's sparse vector additively on top of the A2E output (bias/amplify its dimension). The existing pipeline stays: SOUL.md enum → agent parses the inline tag → per-sentence `/tts` field → TTS forks PCM+emotion to A2F (`infra/pi/agent/tts_plugin.py`, `infra/desktop/a2f/server.py`).
3. **SDK tuning pass**: while in the helper, expose/tune the knobs currently at library defaults — emotion strength, face params, per-blendshape multipliers — guided by the calibration numbers from the gate issue.
4. Rebuild + redeploy the container (`infra/desktop/a2f/deploy/build_image.sh`, `voice-agent-a2f:latest`, `:8003`, WSL2 Docker — ADR-0015; supervision must keep working).

**Gate**: the calibration experiment (blocking issue) decides the emphasis. If forced emotion moved the raw face → this issue as written. If even forced emotion was flat → the SDK-tuning half (3) becomes the primary scope and A2E integration (1–2) may be descoped/deferred — re-read the calibration verdict before starting.

Wire format and DataChannel contract must not change (frames stay `{type, frame, t, arkit}`); the browser side is untouched by this issue.

## Acceptance criteria

- [ ] With no emotion tag (`neutral`), an emotionally-voiced utterance produces a visibly non-zero emotion vector and measurably richer brow/eye/mouth-form blendshape amplitudes than today's baseline (compare against the calibration report's zero-vector numbers).
- [ ] An explicit tag (e.g. `[emotion:happy]`) visibly amplifies the corresponding dimensions on top of the A2E baseline.
- [ ] `?facedebug=1` on a live reply shows clearly richer raw-face mimicry than the pre-change captures from the calibration issue (side-by-side).
- [ ] Helper container rebuilds reproducibly and survives the existing restart/supervision setup; VRAM stays within budget (~0.5 GB order — document the new figure).
- [ ] Existing helper tests (`infra/desktop/a2f/tests/`) pass; wire format unchanged (frontend tests `node --test infra/pi/web/static/js/*.test.mjs` stay green untouched).
- [ ] ADR-0016 status/notes updated if scope shifted per the calibration verdict.

## References

- `docs/adr/0016-a2f-emotion-supply.md` — the decision (read first).
- `docs/retro-epic8-a2f.md` — root-cause chain and roadmap (step 2).
- `docs/adr/0015-a2f-helper-production.md` — helper deployment/supervision constraints.
- Code: `infra/desktop/a2f/a2f_stream/main.cpp` (helper), `infra/desktop/a2f/emotion.py` (enum→vector), `infra/desktop/a2f/engine.py` + `server.py` (service), `infra/pi/agent/tts_plugin.py` (tag → per-sentence emotion field), `infra/desktop/a2f/deploy/` (image build).
- Glossary: `.yoke/context.md` — *Audio2Emotion*, *Emotion vector*, *Emotion tag*.

## Blocked by

- #39 (calibration verdict decides emphasis: A2E integration vs SDK tuning first)

---

# Issue #41 body

## What to build

Make the A2F-driven face **read as expressive on the Live2D avatar**: replace the linear 52→12 pass-through mapping with (a) nonlinear amplification and (b) discrete vtuber-style accents. Roadmap step 3 of `docs/retro-epic8-a2f.md` (session decision #6).

### Background

The ARKit→Live2D mapper (`infra/pi/web/static/js/arkit-map.js`) collapses A2F's 52 blendshapes into ~12 standard Cubism parameters **linearly**, so small A2F amplitudes land on the subtle end of the Live2D ranges. Anime models express emotion through exaggerated, discrete changes (wide-open eyes, tight squints, head tilts) — not micro-muscle motion — which is why even correct A2F data reads as flat on the avatar. This layer amplifies whatever the emotion-supply work (blocking issue) delivers; doing it before that work would amplify noise.

### The change (frontend only, vanilla ES modules)

1. **Expander curves** per parameter group in the mapping layer: compress the noise floor, expand the mid-range (small→smaller, medium→large), clamp gracefully. Tunable per group (brows, eyes, mouth-form, cheeks).
2. **Discrete accents**: threshold-triggered vtuber poses layered on top of the continuous mapping — e.g. sustained high joy → eye-smile squint + raised mouth-form; high amazement → wide eyes (`EyeWide*` past a threshold) + brow pop; optional head-tilt accents via `ParamAngleX/Z` (the face map today deliberately does not touch head angles — adding them must not fight the motion controller; note `avatar.js`'s `beforeModelUpdate` pins whatever `faceParams` contains, last-writer-wins over motions).
3. Keep the architecture intact: pure mapping stays pure and unit-testable (`arkit-map.test.mjs` pattern); the A2F/volume provider split and `?lipsync=volume` retreat (ADR-0013) untouched; the playback scheduler (`schedule.js`, #38) untouched.
4. Tuning workflow: use `?facedebug=1` raw face as the "ground truth" reference next to the avatar — the avatar should convey at least the emotion readable on the debug face.

### Acceptance bar (the retro's main lesson)

Acceptance is a **perceived-effect comparison**, not pipeline correctness: side-by-side against volume-only lip-sync (`?lipsync=volume`), the A2F face must be *obviously* more alive on an emotional reply.

## Acceptance criteria

- [ ] Expander curves implemented in the mapping layer, per-group tunable, covered by unit tests (extend `arkit-map.test.mjs` style; `node --test infra/pi/web/static/js/*.test.mjs` green).
- [ ] At least two discrete accents implemented (e.g. joy-squint, amazement-wide-eyes) with thresholds/hysteresis so they don't flicker frame-to-frame.
- [ ] A/B check recorded (short screen captures or a written verdict in the issue): emotional reply with A2F vs `?lipsync=volume` — A2F variant clearly more expressive; neutral speech does not look grotesque (no permanent overacting).
- [ ] Face still releases cleanly to idle between turns; no fighting with motion-state animations (verify head-angle accents don't stutter idle motions).
- [ ] `?facedebug=1` overlay and `window.__a2fInject` still work for tuning.

## References

- `docs/retro-epic8-a2f.md` — decisions #6, #8 and roadmap step 3; the acceptance-bar lesson.
- `docs/adr/0013-a2f-driven-lipsync-volume-fallback.md` — provider split that must survive; `docs/adr/0012-audio2face-hybrid-facial-animation.md` — hybrid design.
- Code: `infra/pi/web/static/js/arkit-map.js` (the linear mapper to replace), `facial.js` (apply/release), `avatar.js` (`beforeModelUpdate` last-writer sink), `mouth.js` (mouth-open stays owned by the lip-sync provider — do not fight it), `schedule.js`/`blendshapes.js` (playback path, untouched), `arkit-map.test.mjs` (test style).
- Glossary: `.yoke/context.md` — *Blendshape*, *Facial animation*, *Expression*.

## Blocked by

- #40 (amplify meaningful emotion data, not the current near-zero noise)

---

# Issue #42 body

## What to build

Connect the avatar's **body language to the speech itself**: prosody-driven head-sway and nods layered over the existing idle/state motions. Roadmap step 4 of `docs/retro-epic8-a2f.md` (session decision #7).

### Background

Today the avatar's whole-body animation is four motion states (`idle/listening/thinking/speaking`), each a fixed looping Live2D motion picked from the avatar profile (`infra/pi/web/static/js/motion.js`, `avatar-config.js`) — nothing links movement to what is being said, which reads as fake ("random animations on a loop"). The retro chose the cheap high-impact fix: drive head motion from the audio's prosody (energy, pauses) on the frontend — no new models, no LLM gesture tags (explicitly rejected for now, decision #7).

### The change (frontend only)

1. Derive a low-rate prosody signal from the agent's WebRTC audio: energy envelope and pause boundaries. The volume analyser (`lipsync.js`) already computes an RMS-style envelope from the same track — reuse/extend that machinery rather than building a second analyser; note its rAF currently runs only when the volume provider owns the mouth, so the signal source must run during A2F mode too.
2. Map prosody → head parameters layered over motions: slow sway during sustained speech (`ParamAngleX/Z`, small amplitudes), a subtle nod on phrase stress/onsets after pauses, drift back to neutral in silence. Additive on top of idle-motion keyframes where possible (`avatar.js` `beforeModelUpdate` is currently absolute last-writer for `faceParams` — decide additive vs absolute carefully so idle motions aren't frozen).
3. Smoothing/limits so the head never jitters at frame rate and never fights motion-state transitions (`motion.js` stays authoritative for whole-body state).
4. Keep it toggleable (URL param, same pattern as `?lipsync=volume`) for A/B and retreat.

**Priority note**: technically independent of the emotion-supply and Live2D-amplification issues (different signal path), but per the retro roadmap the face work (#40, #41) comes first — pick this up after, or in parallel only if it doesn't contend for the same files.

## Acceptance criteria

- [ ] During a spoken reply the head visibly sways/nods in rhythm with the voice; in silence it settles back and only the idle motion remains.
- [ ] Movement is smooth (no per-frame jitter) and does not fight motion-state animations or the facial accents from the amplification work.
- [ ] Toggleable via URL param; disabled state is exactly today's behavior.
- [ ] Pure signal→angles mapping covered by unit tests in the existing style (`node --test infra/pi/web/static/js/*.test.mjs` green).
- [ ] A/B verdict recorded: with vs without head-sway on the same reply — with is clearly more alive.

## References

- `docs/retro-epic8-a2f.md` — decision #7, roadmap step 4.
- `docs/adr/0008-client-volume-lipsync.md` — the volume analyser this reuses; `docs/adr/0009-agent-authoritative-motion-emotion.md` — motion-state authority that must stay intact.
- Code: `infra/pi/web/static/js/lipsync.js` (analyser/envelope), `motion.js` (state authority), `avatar.js` (`beforeModelUpdate` writer, `ParamAngle*`), `avatar-config.js` (profiles), `main.js` (wiring, URL params).
- Glossary: `.yoke/context.md` — *Motion state*, *Lip-sync*.

## Blocked by

None - can start immediately (by roadmap priority, after #40/#41)

---

# Issue #43 body

## What to build

**Gated spike, do not start until the gate condition is met.** A 3D web avatar prototype (three.js + a VRM/ARKit-52 morph-target model — "metahuman-style in the browser") that consumes the **full 52-blendshape A2F stream with zero mapping loss**, as a second avatar profile next to Live2D. Roadmap step 5 of `docs/retro-epic8-a2f.md` (session decisions #4 and #8).

### Gate condition (why this is blocked)

The retro decided Live2D remains the current renderer and its potential must be exhausted first (the user explicitly wants to keep and reuse what's built). This spike triggers **only if**, after the emotion supply (#40) and Live2D amplification (#41) both land, emotions on the avatar *still* don't read convincingly. The judgment is a human call (art direction + perceived effect) — hence `ready-for-human`.

Also note the logic recorded in the retro: a 3D renderer only pays off if the A2F **data** is rich — it removes the 52→12 mapping loss but adds no data. If the calibration (#39) showed flat data even with forced emotion, fix the data first; a 3D face would be equally flat.

### Scope of the spike (time-boxed, throwaway allowed)

1. Load a VRM (or equivalent) model with the 52 ARKit morph targets in three.js in the existing web frontend (vanilla ES modules, no build step — three.js via ESM; if a build step becomes unavoidable, that's a finding to record, it contradicts ADR-0011).
2. Feed it from the **existing** pipeline: `voiceagent` DataChannel → playback scheduler (`schedule.js`, #38) → apply `arkit` values directly as morph weights. No server changes, no wire-format changes.
3. Reuse the avatar-profile seam (`avatar-config.js`) so 2D/3D is a profile/URL switch; lip-sync fallback and motion-state concepts may be stubbed for the spike.
4. Deliver a side-by-side verdict: same emotional reply on Live2D (amplified) vs the 3D head — which reads more alive, at what implementation and art cost. Record whether the product's vtuber/anime identity survives the style change.

### Outcome

A written recommendation (issue comment or doc): stay on Live2D / adopt 3D / hybrid. If 3D is adopted, that decision gets its own ADR (art direction is a product identity change) and a real implementation epic — this spike does not ship production UI.

## Acceptance criteria

- [ ] Gate explicitly confirmed before starting: #40 and #41 are done and a human judged emotions still not convincing (link the judgment).
- [ ] 3D head renders in the existing frontend and animates live from the real A2F stream via the existing scheduler (no server/wire changes).
- [ ] All 52 blendshapes applied as morph targets (no reduction step).
- [ ] Side-by-side comparison recorded (captures + written verdict): Live2D-amplified vs 3D on the same replies.
- [ ] Recommendation written (stay/adopt/hybrid) with costs; if "adopt" — draft ADR proposed.

## References

- `docs/retro-epic8-a2f.md` — decisions #4 (3D is an allowed course), #8 (gate after Live2D amplification), roadmap step 5.
- `docs/adr/0010-live2d-pixi-cubism-core.md`, `docs/adr/0011-frontend-vanilla-es-modules.md` — what a 3D adoption would supersede/strain.
- `docs/adr/0016-a2f-emotion-supply.md` — the data-side prerequisite.
- Code: `infra/pi/web/static/js/schedule.js` + `blendshapes.js` (the stream to consume as-is), `avatar-config.js` (profile seam), `arkit-map.js` (the 52→12 loss this bypasses).
- Glossary: `.yoke/context.md` — *Avatar*, *Blendshape*.

## Blocked by

- #40 (emotion supply must land first)
- #41 (Live2D ceiling must be measured with amplification in place; gate = human verdict after both)
