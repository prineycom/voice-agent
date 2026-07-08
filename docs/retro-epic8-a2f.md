# Retrospective: Epic 8 — A2F Facial Animation (result assessment & path forward)

Date: 2026-07-06. Scope: Epics/issues #33–#38 (A2F helper in production, frontend consumer,
agent forward, sync measurement, playback scheduler). Session: grill retrospective after #38
shipped and the face animates in sync.

## Verdict

The pipeline works; the *effect* does not. A2F-driven facial animation is delivered, timed
correctly, and stable in production — but visually it is barely distinguishable from the old
volume-only lip-sync. The face reads as "mouth flaps + occasional micro-twitches", not as an
emotive vtuber-style character. Idle/body motions loop randomly with no connection to speech.

## Root-cause chain (established in this session)

1. **The emotion input to A2F is zero most of the time.** The only emotion source is the LLM
   inline tag (5-value enum); `neutral`/missing maps to an all-zeros 10-dim A2E vector
   (`infra/desktop/a2f/emotion.py`). Audio2Emotion (A2E) inference from audio prosody — which the
   full A2F NIM always blends in — is **not run at all** by our slim helper
   (`infra/desktop/a2f/a2f_stream/main.cpp` feeds the emotion accumulator only from the tag).
   With a zero vector, A2F produces near-pure phoneme articulation: jaw/lips move, brows/eyes
   stay flat. Confirmed observation: the raw-ARKit `?facedebug=1` face is flat too — the data
   itself is poor, not (only) the rendering.
2. **The helper uses default SDK executor parameters.** No tuning pass was ever made (emotion
   strength, face params, per-blendshape multipliers exist in the A2X SDK/NIM configs but are
   untouched).
3. **The ARKit→Live2D mapping collapses 52 blendshapes into ~12 Cubism params, linearly.**
   (`infra/pi/web/static/js/arkit-map.js`). Small A2F amplitudes land on the subtle end of the
   Live2D parameter ranges. Anime models express emotion through exaggerated discrete accents,
   not micro-muscle motion — a straight linear pass-through cannot read as expressive there.
4. **Body motion is disconnected from speech by design.** Four motion states, each a fixed
   looping Live2D motion; nothing links gesture to prosody or content.

## What went well

- **Server pipeline is production-solid.** TTS PCM fork → A2F helper (WSL2 Docker, ~0.4 GB
  VRAM, ADR-0015) → agent forward → `voiceagent` DataChannel delivers correct frames with
  correct reply-relative `t`, resilient to malformed frames, restarts supervised.
- **Measure-first paid off.** The `?facedebug=1` instrumentation (raw-ARKit overlay + timing
  stats) precisely diagnosed the burst apply-on-arrival bug; the playback scheduler (#38) fixed
  it with a pure, heavily-tested module (104 assertions), and the review pass caught two real
  edge-case bugs before deploy.
- **Fallback discipline.** The volume lip-sync provider survived every iteration as a one-flag
  retreat (`?lipsync=volume`), exactly as ADR-0013 demanded.
- **ADR trail.** ADRs 0008–0015 made this retrospective's archaeology trivial.

## What went poorly

- **Acceptance criteria measured transport, not value.** Epic 8's phases accepted "face
  animates in sync" — never "emotions are visibly richer than volume lip-sync". We built and
  verified plumbing for a payload whose visual value was never validated end-to-end.
- **The emotion supply was left effectively disconnected.** The single differentiator of A2F
  over a volume analyser — audio-driven emotional mimicry — was shipped with its input mostly
  zeroed and its audio-inference half (A2E) not integrated.
- **The 52→12 mapping was built as a pass-through** with no amplitude design for the target
  (anime/Live2D) art style.
- **The helper shipped on SDK defaults** with no tuning/calibration pass.

**Main lesson:** for perception-facing features, define acceptance as a perceived-effect
comparison ("A/B against the old path, must be obviously better"), not as pipeline correctness.

## Decisions (this session)

| # | Decision | Notes |
|---|----------|-------|
| 1 | Primary gap = weak facial expressiveness during speech; body/gesture link is secondary. | |
| 2 | Root cause is upstream (flat A2F output), not (only) the Live2D mapping — raw facedebug face is flat too. | |
| 3 | **Calibration experiment first**: same utterance through A2F with forced `joy=1.0` vs zeros, compared on `?facedebug=1`. Separates "emotion input is zero" from "model output is inherently flat". Gates all further investment. | |
| 4 | Live2D remains the current renderer, but **3D is an allowed course** (three.js + VRM/ARKit-52 morphs, "metahuman-in-web") if the data proves rich and Live2D's ceiling proves too low. | |
| 5 | **Emotion supply: A2E from audio + LLM tag as boost** (ADR-0016). No separate emotion-classifier service for now. | ADR-0016 |
| 6 | **Live2D amplification: nonlinear gain (expander curves) + discrete vtuber accents** (threshold-triggered squint/wide-eyes/head-tilt) in the frontend mapping layer. | |
| 7 | **Body: prosody-driven head-sway/nods** (ParamAngleX/Z from audio energy/pauses) after the face work; no LLM gesture tags for now. | |
| 8 | **3D prototype gate sits after the Live2D amplification step** — first squeeze the maximum out of what is built; a 3D spike only if emotions still don't read. | |

## Roadmap

1. **Calibrate** — forced-emotion vs zero A/B on facedebug (hours). Decides everything below.
   **Done 2026-07-07** — verdict: emotion supply proceeds (see [calibration report](research/2026-07-07-a2f-emotion-calibration.md)).
2. **Emotion supply** — integrate A2E audio inference into the helper; keep tag as an additive
   boost; tune SDK strength knobs while in there (ADR-0016).
   **Done 2026-07-08** (#40) — A2E baseline + preferred-emotion tag boost + baked brow
   multipliers; all acceptance criteria pass on the production image (see
   [acceptance/tuning/ops data](research/data/40-a2f-emotion-supply/analysis.md)).
3. **Live2D amplification** — expander curves + discrete accents in `arkit-map.js`/frontend;
   re-evaluate perceived effect vs volume lip-sync (the acceptance bar this time).
4. **Head-sway** — prosody-driven head motion layered over idle motions.
5. **Gate: 3D renderer spike** — only if (2)+(3) still don't read: three.js + VRM avatar
   consuming all 52 blendshapes as a second avatar profile.

## References

- ADR-0012 (hybrid facial animation), ADR-0013 (A2F lip-sync + measure-first), ADR-0015
  (helper in production), ADR-0016 (emotion supply — this session).
- Issues #33–#38; `.yoke/ai/38-a2f-playback-scheduler/` (plan/report/review of the scheduler).
- Glossary: `.yoke/context.md` (Audio2Emotion, Emotion vector, Expression — updated).
