// Pure ARKit→Live2D mapper for the loose-sync face: eyes, gaze, brows, mouth
// *shape* and cheeks. Deliberately excludes ParamMouthOpenY — mouth *opening* is
// owned by the pluggable provider in mouth.js (A2F JawOpen primary, volume
// analyser fallback/toggle; ADR-0013), so this face map never fights the lip-sync
// path. Keys are the PascalCase ARKit names the A2F service emits (see
// infra/desktop/a2f/arkit.py ARKIT_52); a missing key reads as 0. Pure: no DOM,
// no globals, returns one object literal per call. Expression params (eye smile,
// brows, mouth form, cheeks) are run through their group's expander curve
// (expander.js) to compress the noise floor and amplify the mid-range; eye-lid
// open and gaze are left linear.

import { expand, GROUP_CURVES } from './expander.js';

const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);
const clamp01 = v => (v < 0 ? 0 : v > 1 ? 1 : v);

export function arkitToLive2D(arkit) {
  const g = k => arkit[k] || 0;
  return {
    // Blink drives closed; EyeWide nudges the lid a touch past neutral-open.
    ParamEyeLOpen: clamp01(1 - g('EyeBlinkLeft') + g('EyeWideLeft') * 0.1),
    ParamEyeROpen: clamp01(1 - g('EyeBlinkRight') + g('EyeWideRight') * 0.1),
    // Squint reads as a smiling/narrowed eye.
    ParamEyeLSmile: expand(g('EyeSquintLeft'), GROUP_CURVES.eyes),
    ParamEyeRSmile: expand(g('EyeSquintRight'), GROUP_CURVES.eyes),
    // Gaze: average both eyes; Out/In are opposite signs per side, Left vs Right
    // eye look in mirrored directions so combine them to a single screen X.
    ParamEyeBallX: clamp(((g('EyeLookOutLeft') - g('EyeLookInLeft')) + (g('EyeLookInRight') - g('EyeLookOutRight'))) / 2, -1, 1),
    ParamEyeBallY: clamp(((g('EyeLookUpLeft') + g('EyeLookUpRight')) - (g('EyeLookDownLeft') + g('EyeLookDownRight'))) / 2, -1, 1),
    // Brow height: inner-up + outer-up lift, brow-down lowers. Per side.
    ParamBrowLY: expand(clamp(g('BrowInnerUp') + g('BrowOuterUpLeft') - g('BrowDownLeft'), -1, 1), GROUP_CURVES.brows),
    ParamBrowRY: expand(clamp(g('BrowInnerUp') + g('BrowOuterUpRight') - g('BrowDownRight'), -1, 1), GROUP_CURVES.brows),
    // Brow angle: brow-down tilts the inner brow toward a frown. `0 -` (not
    // unary minus) so a rest frame yields +0, not -0 (expand(+0) stays +0).
    ParamBrowLAngle: expand(0 - g('BrowDownLeft'), GROUP_CURVES.brows),
    ParamBrowRAngle: expand(0 - g('BrowDownRight'), GROUP_CURVES.brows),
    // Mouth *form* only (smile − frown − pucker); opening handled by lip-sync.
    ParamMouthForm: expand(clamp((g('MouthSmileLeft') + g('MouthSmileRight')) / 2 - (g('MouthFrownLeft') + g('MouthFrownRight')) / 2 - g('MouthPucker'), -1, 1), GROUP_CURVES.mouthForm),
    ParamCheek: expand((g('CheekSquintLeft') + g('CheekSquintRight')) / 2, GROUP_CURVES.cheeks),
  };
}
