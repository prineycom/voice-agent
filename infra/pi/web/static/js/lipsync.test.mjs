// Zero-dependency Node ESM test for the AGC mouth mapper in lipsync.js.
// Run: node infra/pi/web/static/js/lipsync.test.mjs
import assert from 'node:assert/strict';
import { makeMouthMapper } from './lipsync.js';

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

// Silence and sub-gate frames snap the mouth fully closed.
eq(makeMouthMapper()(0), 0, 'silence -> 0');
eq(makeMouthMapper()(0.005), 0, 'sub-gate (0.015) -> 0');

// The loudest recent syllable opens the mouth fully (norm == 1, curve^1 == 1),
// no matter the absolute level — that's the whole point of the AGC.
eq(makeMouthMapper()(0.2), 1, 'first loud frame latches peak -> 1 (loud track)');
eq(makeMouthMapper()(0.12), 1, 'first loud frame latches peak -> 1 (quiet track above floor)');
// Below floorPeak (0.08) the mapper stops short of fully open — that floor is what
// keeps faint room noise from reading as full-volume speech.
ok(makeMouthMapper()(0.05) < 1, 'sub-floor frame opens partially, not fully');

// Level independence: the same RMS *shape* scaled by any factor yields the same
// mouth target once the peak has latched. Loud then half-as-loud syllable.
const loud = makeMouthMapper();
const quiet = makeMouthMapper();
loud(1.0); quiet(0.4);                       // latch each peak
const loudMid = loud(0.5);                    // 50% of its own peak
const quietMid = quiet(0.2);                  // 50% of its own peak
approx(loudMid, quietMid, 'half-volume syllable maps identically across levels');
ok(loudMid > 0.6, 'curve expands a mid-level syllable to a wide opening');

// Curve < 1 means a half-level syllable opens MORE than half — visibly wider.
ok(loudMid > 0.5, 'perceptual curve lifts mid speech above linear 0.5');

// The peak decays toward floorPeak during silence so the mapper re-sensitises.
const m = makeMouthMapper();
m(1.0);                                       // peak latched high
for (let i = 0; i < 600; i++) m(0);           // ~10s of silence at 60fps
ok(m(0.08) > 0.9, 'after silence the floor-level peak makes quiet speech open wide again');

console.log(`lipsync.js: all ${assertions} assertions passed`);
process.exit(0);
