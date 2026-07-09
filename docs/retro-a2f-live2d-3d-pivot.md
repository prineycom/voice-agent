# Retrospective: A2F/A2E emotion supply on Live2D (#39–#41) — and the pivot to a 3D face

Date: 2026-07-09. Scope: #39 (A2F calibration), #40 (A2E emotion supply), #41 (Live2D
amplification: expander curves + discrete accents). Session: grill retrospective after #41
shipped and the user reported a regression ("avatar barely moves, even lipsync stopped;
facedebug shows no more emotion"). Follow-on to `docs/retro-epic8-a2f.md`.

## Verdict

The emotion pipeline is real and works, but the on-screen effect on the Live2D avatar is
still not there — and #41 made the Live2D page visibly worse, not better. The failure is **not**
proof that A2F/A2E is the wrong pipeline. It decomposes into two separate, addressable causes
plus one genuine architectural ceiling. Decision: **freeze Live2D as-is and build the emotive
face on a new 3D avatar (Ready Player Me + three.js) driven by the existing A2F ARKit pipeline,
on a separate page/URL.** Live2D is revisited later.

## What actually happened (measured this session)

1. **A2F/A2E genuinely produce distinct emotional faces — on emotional audio.** Measured from
   the #39 captures (`docs/research/data/39-a2f-calibration/`): joy → `BrowDownLeft/Right` peak
   ≈ 0.48, `MouthFrown` ≈ 0.59; anger → `BrowInnerUp` ≈ 0.56. The pipeline differentiates
   emotions. In production the TTS voice (Qwen3-TTS) is **monotone**, so A2E — which infers
   emotion from audio prosody — sees near-neutral and the face is flat. `?facedebug=1` (raw
   ARKit, decoupled from the renderer) confirms the flatness is in the **source signal**, not
   the Live2D rendering. This is a source problem, renderer-independent.

2. **A2F expression amplitudes are small — and #41's expander over-compressed them.** Same
   captures: the expression channels peak low — `EyeSquint` ≈ 0.04–0.15, `MouthSmile` ≈
   0.10–0.21, `CheekSquint` ≈ 0.12; only `BrowDown`/`MouthFrown`/`BrowInnerUp` reach 0.5. The
   #41 expander uses `floor = 0.05`, which **zeroes everything below 0.05**. For anger,
   `EyeSquintLeft` (max 0.036) is entirely below the floor → zeroed. So on the subtle channels
   #41 made the face *less* alive, exactly the opposite of intent. Root cause: the floor was
   tuned for a signal amplitude A2F does not actually deliver. Lesson: **tune knobs against the
   real captured amplitude distribution, not assumed ranges.**

3. **"Even lipsync stopped" is mechanically explained and fixable.** In
   `infra/pi/web/static/js/blendshapes.js:43-47`, `apply(f)` calls `facial.apply(f.arkit)`
   **before** `mouth.ingestA2FFrame(f)` with no `try/catch`. A throw anywhere in the new facial
   path (expander/accents) would abort the frame before the mouth update, killing lipsync too —
   while `?facedebug=1` (which fires first, line 44) keeps drawing. This is a #41-class
   robustness gap, not evidence against A2F. Lesson for the 3D consumer: **guard the per-frame
   apply so the face renderer can never take down lipsync.**

4. **The one genuine architectural ceiling: Live2D's 52→12 collapse is lossy.**
   `arkit-map.js` linearly collapses A2F's 52 ARKit blendshapes into ~12 Cubism params. ARKit-52
   is the **native** input for 3D faces (Apple ARKit, MetaHuman, Ready Player Me). Driving a 2D
   Live2D rig from it is inherently lossy and needs per-model hand-mapping. This ceiling is
   independent of the two bugs above and is the real case for a 3D face.

## Reframed conclusion

The user's tentative "A2F+A2E integrate poorly with Live2D" is a misattribution. The observed
badness = **(a) a flat source signal (monotone TTS)** + **(b) a mis-tuned/over-compressing #41
frontend**. Neither is a Live2D-vs-A2F verdict. But there *is* a separate, real reason to move
the face to 3D: ARKit-52 wants a 3D face, and RPM consumes it 1:1 with no lossy collapse.

## Decisions (from the grill session)

See `docs/adr/0017-3d-face-arkit-pipeline.md` for the accepted architecture decision. Summary:

1. **Keep Live2D as-is.** Frozen, knowingly degraded post-#41 (no revert, no fix now). Revisit
   later. The #41 expander/accent work and its lessons carry into the 3D track.
2. **Build the emotive face as a new page on a separate URL**, reusing the LiveKit audio,
   transcript, and the A2F data-channel consumer (`blendshapes.js`/`schedule.js`) unchanged —
   swap **only** the renderer.
3. **Look:** semi-realistic **Ready Player Me** avatar (glTF, native ARKit-52 morph targets) —
   the practical "MetaHuman-in-browser". (Photoreal MetaHuman is out: it needs UE5 pixel
   streaming from a GPU server, impractical for the browser-client / Pi model. Anime/VRM was
   considered and rejected for now: VRM expressions are preset-based and ARKit→VRM is lossy.)
4. **Engine:** **three.js directly** + the RPM `.glb`; map the A2F ARKit stream → morph targets
   **1:1**; head/gaze via bone rotation. `TalkingHead.js` (met4citizen) is reference only, not a
   dependency — it owns TTS/viseme/streaming assumptions that would fight LiveKit audio + our A2F
   stream.
5. **Lipsync:** A2F drives the mouth **1:1** (RPM has native `jawOpen` + mouth morphs). **No
   volume-analyser fallback** — the 3D face shows *only* what A2F sends (user clarification);
   when there is no A2F stream the mouth simply rests. This deliberately departs from ADR-0013's
   volume-fallback provider split, which stays on the Live2D page.
6. **Head/gaze:** a `setHeadPose`/`setGaze` API driven by a procedural idle baseline (subtle
   sway + gaze wander + blink) with agent/LLM overrides via the existing authoritative motion
   channel (ADR-0009).
7. **Emotion source (flat A2E):** **deferred.** Get the 3D face rendering A2F faithfully first;
   source expressiveness (raise the LLM-tag preferred-emotion boost and/or expressive TTS) is a
   later, separate track. **Risk accepted and recorded:** until the source is fixed, the 3D face
   may still read flat on ordinary monotone replies — the renderer cannot manufacture emotion the
   audio does not carry.
8. **First step:** skip a throwaway spike; go straight to a **thin new page** (new URL; RPM +
   A2F 1:1 mouth+face, no volume fallback; static head/gaze). Fold the key de-risking — verifying that A2F's
   PascalCase ARKit-52 names map onto RPM's camelCase morph-target names — into the page's first
   task. Procedural idle + agent override come next.

## Parked (sensible defaults, decide at build time)

- URL route name (default `/face3d`).
- Which RPM avatar to use, and self-hosting its `.glb`.
- Vendoring `three.module.js` + `GLTFLoader` self-hosted (no build step; consistent with
  ADR-0011).
- The agent-side motion-event schema for head/gaze targets.

## Open risk to watch

The deferred source problem (item 7) is the same wall #40 and this retro both hit. A beautiful
3D face on a flat A2E signal is still a flat face. When the 3D renderer is proven, the very next
priority should be source expressiveness, or the "liveliness" complaint will recur.
