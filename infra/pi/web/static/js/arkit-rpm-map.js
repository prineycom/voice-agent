// Pure ARKit→RPM morph-target name mapper. The A2F service emits PascalCase
// ARKit-52 blendshape names (see infra/desktop/a2f/arkit.py ARKIT_52); Ready
// Player Me (RPM) avatars expose the same ARKit shapes as glTF morph targets
// under camelCase names (first char lowercased, e.g. MouthSmileLeft →
// mouthSmileLeft, JawOpen → jawOpen). This module is the naming bridge only:
// no GL, no three.js, no DOM — pure functions returning plain objects so it can
// be unit-tested with node directly.
//
// The 52 standard ARKit shapes (including TongueOut) map 1:1 to RPM morph
// targets. The A2F James model also emits 16 *extended* Tongue* shapes
// (TONGUE_16) that have no RPM morph target; those are intentionally unmapped
// (RPM_UNMAPPED) and dropped from the output. Unknown/extra keys are skipped.

// Mirror of arkit.py ARKIT_52 (PascalCase, canonical order). Source of truth for
// which names map to an RPM morph target.
const ARKIT_52 = [
  'EyeBlinkLeft', 'EyeLookDownLeft', 'EyeLookInLeft', 'EyeLookOutLeft', 'EyeLookUpLeft',
  'EyeSquintLeft', 'EyeWideLeft',
  'EyeBlinkRight', 'EyeLookDownRight', 'EyeLookInRight', 'EyeLookOutRight', 'EyeLookUpRight',
  'EyeSquintRight', 'EyeWideRight',
  'JawForward', 'JawLeft', 'JawRight', 'JawOpen',
  'MouthClose', 'MouthFunnel', 'MouthPucker', 'MouthLeft', 'MouthRight',
  'MouthSmileLeft', 'MouthSmileRight', 'MouthFrownLeft', 'MouthFrownRight',
  'MouthDimpleLeft', 'MouthDimpleRight', 'MouthStretchLeft', 'MouthStretchRight',
  'MouthRollLower', 'MouthRollUpper', 'MouthShrugLower', 'MouthShrugUpper',
  'MouthPressLeft', 'MouthPressRight', 'MouthLowerDownLeft', 'MouthLowerDownRight',
  'MouthUpperUpLeft', 'MouthUpperUpRight',
  'BrowDownLeft', 'BrowDownRight', 'BrowInnerUp', 'BrowOuterUpLeft', 'BrowOuterUpRight',
  'CheekPuff', 'CheekSquintLeft', 'CheekSquintRight',
  'NoseSneerLeft', 'NoseSneerRight',
  'TongueOut',
];

// Mirror of arkit.py TONGUE_16 — the extended tongue shapes with no RPM target.
const TONGUE_16 = [
  'TongueTipUp', 'TongueTipDown', 'TongueTipLeft', 'TongueTipRight',
  'TongueRollUp', 'TongueRollDown', 'TongueRollLeft', 'TongueRollRight',
  'TongueUp', 'TongueDown', 'TongueLeft', 'TongueRight',
  'TongueIn', 'TongueStretch', 'TongueWide', 'TongueNarrow',
];

// First-char-lowercase: the A2F PascalCase name → RPM camelCase morph-target name.
export function arkitNameToRpm(name) {
  if (!name) return name;
  return name[0].toLowerCase() + name.slice(1);
}

// The set of recognized ARKit-52 PascalCase names (the only keys we map).
const ARKIT_52_SET = new Set(ARKIT_52);

// The 16 extended Tongue* shapes as camelCase RPM-style names, intentionally
// unmapped (no RPM morph target exists for them).
export const RPM_UNMAPPED = TONGUE_16.map(arkitNameToRpm);

// Map an A2F per-frame `arkit` object ({ PascalName: value }) to RPM morph
// targets ({ camelName: value }). Only recognized ARKit-52 names are mapped;
// extended Tongue* and any other unknown keys are skipped. Values pass through
// unchanged. With opts.excludeJaw the mouth sink owns the jaw, so JawOpen is
// dropped from the output.
export function arkitToRpmMorphs(arkit, opts = {}) {
  const excludeJaw = opts.excludeJaw === true;
  const out = {};
  if (!arkit) return out;
  for (const name of Object.keys(arkit)) {
    if (!ARKIT_52_SET.has(name)) continue; // skip Tongue* and unknown keys
    if (excludeJaw && name === 'JawOpen') continue;
    out[arkitNameToRpm(name)] = arkit[name];
  }
  return out;
}
