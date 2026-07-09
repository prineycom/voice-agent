// Thin controller between the pure ARKit→Live2D map and the avatar rig sink.
// Contract: pin the loose-sync face params while a face stream is active, then
// release when idle so Live2D's built-in auto-blink / idle motion takes back
// over — holding the last frame would freeze a stale pose between utterances.
// This module only forwards; stream boundary detection (when to apply vs
// release) lives in the consumer that owns the A2F stream.
// Optional discrete accents (accents.js) are layered ON TOP of the base map.
import { arkitToLive2D } from './arkit-map.js';

export function createFacial(avatar, { accents = null } = {}) {
  return {
    // Pin this frame's face pose (absolute per-frame param map).
    apply(arkit) {
      const params = arkitToLive2D(arkit);
      if (accents) Object.assign(params, accents.step(arkit));
      avatar.setFaceParams(params);
    },
    // Release the face so auto-blink / idle motion resumes.
    release() {
      if (accents) accents.reset();
      avatar.setFaceParams(null);
    },
  };
}
