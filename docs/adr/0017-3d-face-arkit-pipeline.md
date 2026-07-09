---
tags:
  - voice-agent
  - adr
status: accepted
relates-to: "0009-agent-authoritative-motion-emotion, 0010-live2d-pixi-cubism-core, 0011-frontend-vanilla-es-modules, 0012-audio2face-hybrid-facial-animation, 0013-a2f-driven-lipsync-volume-fallback, 0016-a2f-emotion-supply"
---

# 3D Browser Face (Ready Player Me + three.js) Driven by the A2F ARKit Pipeline

After #39–#41 the A2F/A2E emotion pipeline is delivered and correct, but the on-screen effect on
the Live2D avatar is still flat, and #41 (expander curves + discrete accents) visibly degraded
the Live2D page. The root causes are a flat source signal (monotone Qwen3-TTS → Audio2Emotion
sees near-neutral) and a mis-tuned #41 frontend (`floor = 0.05` zeroes A2F's genuinely small
expression amplitudes; an unguarded per-frame `apply` can also drop lipsync) — **not** a
Live2D-vs-A2F verdict. See `docs/retro-a2f-live2d-3d-pivot.md` for the measured evidence.

There remains one genuine architectural ceiling: `arkit-map.js` collapses A2F's **52 ARKit
blendshapes into ~12 Cubism params** linearly. ARKit-52 is the *native* input for 3D faces
(Apple ARKit, MetaHuman, Ready Player Me); driving a 2D Live2D rig from it is inherently lossy.

## Decision

Build the emotive face on a **new 3D avatar**, on a **separate page/URL**, driven by the
existing A2F ARKit pipeline. Freeze the Live2D page as-is (knowingly degraded post-#41) and
revisit it later; do not revert or fix #41 on Live2D now.

- **Avatar:** semi-realistic **Ready Player Me** (glTF with native ARKit-52 morph targets) — the
  practical "MetaHuman-in-browser". A2F's 52 blendshapes map **1:1** onto the RPM morph targets,
  removing the 52→12 lossy collapse. (Photoreal MetaHuman rejected: needs UE5 pixel streaming
  from a GPU server, impractical for the browser-client / Pi model. Anime/VRM rejected for now:
  VRM expressions are preset-based, ARKit→VRM is lossy.)
- **Engine:** **three.js directly** (self-hosted ES module build + `GLTFLoader`, no build step —
  consistent with ADR-0011), loading the RPM `.glb`. `TalkingHead.js` (met4citizen) is a
  **reference only**, not a dependency — it owns TTS/viseme/streaming assumptions that would
  fight our LiveKit audio + A2F stream.
- **Reuse, don't rebuild:** the new page reuses the LiveKit audio, transcript, and the A2F
  data-channel consumer (`blendshapes.js` / `schedule.js`) unchanged; only the renderer is
  swapped (three.js face controller in place of the Live2D/Cubism sink).
- **Lipsync:** A2F drives the mouth **1:1** (RPM native `jawOpen` + mouth morphs). **No
  volume-analyser fallback** — the 3D face renders *only* what A2F sends; with no A2F stream the
  mouth rests. This deliberately departs from ADR-0013 (whose volume fallback stays on Live2D).
- **Head & gaze:** a `setHeadPose` / `setGaze` API driven by a **procedural idle** baseline
  (subtle head sway + gaze wander + blink) with **agent/LLM overrides** via the existing
  authoritative motion channel (ADR-0009). Head/gaze are bone rotations, owned by the face
  controller, not by the A2F blendshape stream.
- **Robustness:** the 3D per-frame apply must be guarded so the face renderer can never take
  down lipsync (the `blendshapes.js` failure mode found in the retro).

## Sequencing

1. **Thin new page first** (skip a throwaway spike): new URL (default `/face3d`), RPM + A2F 1:1
   mouth+face, no volume fallback, static head/gaze. The first task de-risks the mapping —
   verify A2F's PascalCase ARKit-52 names (`MouthSmileLeft`) map onto RPM's camelCase morph
   targets (`mouthSmileLeft`), including any case/name deltas.
2. Procedural idle head/gaze + agent override.
3. `?facedebug=1`-equivalent overlay parity for tuning.

## Consequences

- **Positive:** removes the 52→12 lossy ceiling; A2F maps 1:1; head/gaze become first-class,
  directly controllable; the working (if flat) Live2D page is untouched, so this is
  zero-risk to the current deployment; keeps the vanilla-ESM / no-build-step ethos (ADR-0011).
- **Deferred risk (recorded):** the **flat emotion source** (monotone TTS → flat A2E) is *not*
  fixed by this change. A 3D face on a flat A2E signal is still flat on ordinary replies. The
  next priority after the renderer is proven must be source expressiveness (raise the LLM-tag
  preferred-emotion boost of ADR-0016, and/or expressive TTS) — otherwise the "liveliness"
  complaint recurs.
- **Cost:** a new renderer stack (three.js, glTF, RPM asset self-hosting) and a WebGL runtime in
  the browser; more GPU/CPU on the client than Live2D's 2D canvas.
- **Live2D debt:** the Live2D page stays degraded post-#41 until a future revisit; the #41
  expander/accent lessons (amplitude-aware tuning) carry into the 3D work.
