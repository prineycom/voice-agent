// Guarded facial + jaw-mouth sink pair for the 3D (RPM) face, driven by the A2F
// blendshapes consumer (blendshapes.js). createBlendshapes' apply() calls
//   facial.apply(f.arkit)   THEN   mouth.ingestA2FFrame(f)
// with NO try/catch between them (blendshapes.js lines 43-47). So a throw in the
// facial renderer would skip the mouth update and freeze lipsync — the retro bug.
// Therefore each sink method here swallows its OWN throws internally and always
// returns normally, so a broken face renderer can never stop the mouth.
//
// Ownership split (DD-3): the facial sink writes all 52 morphs EXCEPT jawOpen
// (arkitToRpmMorphs excludeJaw:true drops it); the mouth sink writes ONLY
// jawOpen — the RAW A2F JawOpen, clamped to [0,1]. RPM has its own mouthClose
// morph, written by facial, so we do NOT fold MouthClose into the jaw. Each of
// the 52 morphs is written exactly once per frame.
//
// Pure logic: `renderer` is injected as an object exposing applyMorphs(map), so
// tests pass a recording/throwing fake. No three.js / WebGL import here.

import { arkitToRpmMorphs } from './arkit-rpm-map.js';

const clamp01 = (x) => Math.max(0, Math.min(1, x || 0));

export function createFaceSinks(renderer, { log } = {}) {
  // The neutral facial rest pose: every mapped morph (excluding jawOpen, which
  // the mouth owns) set to 0. Computed lazily from a full ARKit-52 zero frame so
  // release() writes exactly the same key set facial.apply() writes.
  let zeroMorphs = null;
  function neutralMorphs() {
    if (zeroMorphs) return zeroMorphs;
    // Build a zeroed morph map from whatever facial has ever written; but on a
    // cold release we may not have a reference frame, so derive from the mapper
    // using the RPM key set it produces for a zero-valued ARKit object. We can't
    // enumerate ARKit-52 here without importing it, so accumulate seen keys.
    zeroMorphs = {};
    for (const k of seenMorphKeys) zeroMorphs[k] = 0;
    return zeroMorphs;
  }

  // Track the RPM morph keys facial has written, so release() can zero exactly
  // that set even before we know the full ARKit-52 list. Invalidated (zeroMorphs
  // cache dropped) whenever the seen set grows.
  const seenMorphKeys = new Set();

  const facial = {
    apply(arkit) {
      try {
        const morphMap = arkitToRpmMorphs(arkit, { excludeJaw: true });
        let grew = false;
        for (const k of Object.keys(morphMap)) {
          if (!seenMorphKeys.has(k)) { seenMorphKeys.add(k); grew = true; }
        }
        if (grew) zeroMorphs = null; // invalidate neutral cache
        renderer.applyMorphs(morphMap);
      } catch (e) {
        if (log) log('face3d ошибка facial.apply: ' + (e && e.message));
        // swallow — the mouth must still update after us
      }
    },
    release() {
      try {
        renderer.applyMorphs(neutralMorphs());
      } catch (e) {
        if (log) log('face3d ошибка facial.release: ' + (e && e.message));
      }
    },
  };

  const mouth = {
    beginA2FStream() {
      // stream active — no per-begin renderer work needed for the jaw sink
    },
    ingestA2FFrame(f) {
      try {
        const jawOpen = clamp01(f && f.arkit ? f.arkit.JawOpen : 0);
        renderer.applyMorphs({ jawOpen });
      } catch (e) {
        if (log) log('face3d ошибка mouth.ingestA2FFrame: ' + (e && e.message));
      }
    },
    endA2FStream() {
      try {
        renderer.applyMorphs({ jawOpen: 0 }); // rest the jaw at stream end
      } catch (e) {
        if (log) log('face3d ошибка mouth.endA2FStream: ' + (e && e.message));
      }
    },
  };

  return { facial, mouth };
}
