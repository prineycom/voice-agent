// Thin controller between the pure ARKit→Live2D map and the avatar rig sink.
// Contract: pin the loose-sync face params while a face stream is active, then
// release when idle so Live2D's built-in auto-blink / idle motion takes back
// over — holding the last frame would freeze a stale pose between utterances.
// This module only forwards; stream boundary detection (when to apply vs
// release) lives in the consumer that owns the A2F stream.
import { arkitToLive2D } from './arkit-map.js';

export function createFacial(avatar) {
  return {
    // Pin this frame's face pose (absolute per-frame param map).
    apply(arkit) {
      avatar.setFaceParams(arkitToLive2D(arkit));
    },
    // Release the face so auto-blink / idle motion resumes.
    release() {
      avatar.setFaceParams(null);
    },
  };
}
