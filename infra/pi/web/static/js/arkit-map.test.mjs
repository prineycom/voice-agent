// Zero-dependency Node ESM test for the pure ARKit→Live2D mapper in arkit-map.js.
// Run: node infra/pi/web/static/js/arkit-map.test.mjs
import assert from 'node:assert/strict';
import { arkitToLive2D } from './arkit-map.js';

let assertions = 0;
function eq(actual, expected, msg) {
  assert.equal(actual, expected, msg);
  assertions++;
}
function ok(value, msg) {
  assert.ok(value, msg);
  assertions++;
}
function approx(actual, expected, msg, eps = 1e-9) {
  assert.ok(Math.abs(actual - expected) < eps, `${msg} (got ${actual}, want ~${expected})`);
  assertions++;
}

// Neutral frame: eyes fully open, everything else at rest.
const neutral = arkitToLive2D({});
eq(neutral.ParamEyeLOpen, 1, 'neutral -> ParamEyeLOpen 1');
eq(neutral.ParamEyeROpen, 1, 'neutral -> ParamEyeROpen 1');
for (const [k, v] of Object.entries(neutral)) {
  if (k === 'ParamEyeLOpen' || k === 'ParamEyeROpen') continue;
  eq(v, 0, `neutral -> ${k} 0`);
}

// Left blink closes only the left eye.
const blink = arkitToLive2D({ EyeBlinkLeft: 1 });
eq(blink.ParamEyeLOpen, 0, 'EyeBlinkLeft:1 -> ParamEyeLOpen 0');
eq(blink.ParamEyeROpen, 1, 'EyeBlinkLeft:1 -> ParamEyeROpen 1');

// Look-left frame maps gaze X toward +1.
const lookLeft = arkitToLive2D({ EyeLookOutLeft: 1, EyeLookInRight: 1 });
approx(lookLeft.ParamEyeBallX, 1, 'look-left -> ParamEyeBallX ~+1');

// Smile vs frown drive mouth form to the extremes.
approx(arkitToLive2D({ MouthSmileLeft: 1, MouthSmileRight: 1 }).ParamMouthForm, 1, 'smile -> ParamMouthForm ~+1');
approx(arkitToLive2D({ MouthFrownLeft: 1, MouthFrownRight: 1 }).ParamMouthForm, -1, 'frown -> ParamMouthForm ~-1');

// Expander amplifies the mid-range: a 0.3 smile lifts above the old linear 0.3.
ok(arkitToLive2D({ MouthSmileLeft: 0.3, MouthSmileRight: 0.3 }).ParamMouthForm > 0.3, 'mid smile -> ParamMouthForm amplified >0.3');
// Noise-floor: a tiny smile compresses toward the rest frame.
ok(arkitToLive2D({ MouthSmileLeft: 0.03, MouthSmileRight: 0.03 }).ParamMouthForm < 0.03, 'tiny smile -> ParamMouthForm compressed <0.03');
// Endpoints are preserved (not pushed past 1) — full smile holds exactly +1.
eq(arkitToLive2D({ MouthSmileLeft: 1, MouthSmileRight: 1 }).ParamMouthForm, 1, 'full smile -> ParamMouthForm exactly 1');
// Neutral rest frame stays +0 (not -0) on the expander-shaped params.
ok(Object.is(neutral.ParamMouthForm, 0), 'neutral -> ParamMouthForm +0 not -0');
ok(Object.is(neutral.ParamBrowLAngle, 0), 'neutral -> ParamBrowLAngle +0 not -0');

// Eye-squint smile is expander-wired (GROUP_CURVES.eyes): a 0.3 squint amplifies
// above the old linear 0.3, a tiny squint compresses toward 0, and rest stays +0.
ok(arkitToLive2D({ EyeSquintLeft: 0.3 }).ParamEyeLSmile > 0.3, 'mid squint -> ParamEyeLSmile amplified >0.3');
ok(arkitToLive2D({ EyeSquintRight: 0.3 }).ParamEyeRSmile > 0.3, 'mid squint -> ParamEyeRSmile amplified >0.3');
ok(arkitToLive2D({ EyeSquintLeft: 0.03 }).ParamEyeLSmile < 0.03, 'tiny squint -> ParamEyeLSmile compressed <0.03');
ok(Object.is(neutral.ParamEyeLSmile, 0), 'neutral -> ParamEyeLSmile +0 not -0');

// Cheek squint (avg of both sides) is expander-wired (GROUP_CURVES.cheeks): a 0.3
// cheek amplifies above the old linear 0.3, a tiny cheek compresses, rest stays +0.
ok(arkitToLive2D({ CheekSquintLeft: 0.3, CheekSquintRight: 0.3 }).ParamCheek > 0.3, 'mid cheek -> ParamCheek amplified >0.3');
ok(arkitToLive2D({ CheekSquintLeft: 0.03, CheekSquintRight: 0.03 }).ParamCheek < 0.03, 'tiny cheek -> ParamCheek compressed <0.03');
ok(Object.is(neutral.ParamCheek, 0), 'neutral -> ParamCheek +0 not -0');

// Brow-down sets the angle to -1 and clamps the (negative) height to -1.
const browDown = arkitToLive2D({ BrowDownLeft: 1 });
eq(browDown.ParamBrowLAngle, -1, 'BrowDownLeft:1 -> ParamBrowLAngle -1');
eq(browDown.ParamBrowLY, -1, 'BrowDownLeft:1 -> ParamBrowLY clamps to -1');

// Mouth *opening* is never emitted here — it stays on the volume analyser.
ok(!('ParamMouthOpenY' in neutral), 'ParamMouthOpenY is not a returned key');

console.log(`arkit-map.js: all ${assertions} assertions passed`);
process.exit(0);
