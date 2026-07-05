// Zero-dependency Node ESM test for the pure fns + provider gating in mouth.js.
// Run: node infra/pi/web/static/js/mouth.test.mjs
import assert from 'node:assert/strict';
import { createMouth, a2fMouthOpen, crossCorrelateOffset } from './mouth.js';

let assertions = 0;
function eq(actual, expected, msg) {
  assert.equal(actual, expected, msg);
  assertions++;
}
function ok(value, msg) {
  assert.ok(value, msg);
  assertions++;
}

// 1. a2fMouthOpen shaping: JawOpen gated by (1 - MouthClose), clamped 0..1.
eq(a2fMouthOpen({}), 0, 'no blendshapes -> mouth closed');
eq(a2fMouthOpen({ JawOpen: 1 }), 1, 'full jaw, no close -> fully open');
eq(a2fMouthOpen({ JawOpen: 1, MouthClose: 0.5 }), 0.5, 'MouthClose halves the opening');

// 2. crossCorrelateOffset recovers a known +40ms lead. A is a bump envelope over
// 0..500ms; B is the SAME bump shifted +40ms (later in time). A leads B, so the
// signed offset must come back ≈ +40ms within the 10ms grid tolerance.
const bump = t => Math.exp(-((t - 250) ** 2) / (2 * 80 * 80));   // Gaussian peak at 250ms
const A = [], B = [];
for (let t = 0; t <= 500; t += 10) {
  A.push({ t, v: bump(t) });
  B.push({ t, v: bump(t - 40) });   // B's peak is 40ms later → A leads B by 40ms
}
const off = crossCorrelateOffset(A, B);
ok(Math.abs(off - 40) <= 10, `recovers +40ms offset within grid tolerance (got ${off})`);

// 3. Provider gating with a stub avatar and a fake clock. forceVolume pins the
// provider to 'volume' so A2F frames never reach the mouth, but volume ones do.
let clock = 0;
const avatar = { calls: [], setMouthOpen(v) { this.calls.push(v); } };
const mouth = createMouth(avatar, { forceVolume: true, now: () => clock });
mouth.beginA2FStream();
clock = 10;
mouth.ingestA2FFrame({ t: 0, arkit: { JawOpen: 1 } });
eq(avatar.calls.length, 0, 'forceVolume: A2F frame does not drive the mouth');
clock = 20;
mouth.volumeSink.setMouthOpen(0.3);
eq(avatar.calls.length, 1, 'volume frame drives the mouth');
eq(avatar.calls[0], 0.3, 'volume value forwarded verbatim');

console.log(`mouth.js: all ${assertions} assertions passed`);
process.exit(0);
