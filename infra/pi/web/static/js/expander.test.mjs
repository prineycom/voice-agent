// Zero-dependency Node ESM test for the pure nonlinear expander in expander.js.
// Run: node infra/pi/web/static/js/expander.test.mjs
import assert from 'node:assert/strict';
import { expand, GROUP_CURVES } from './expander.js';

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

// Representative curves: signed (odd, [-1,1]) and unsigned ([0,1]).
const signed = GROUP_CURVES.mouthForm;
const unsigned = GROUP_CURVES.eyes;

// Rest frame stays +0, never -0 — downstream `0 - g()` relies on this.
ok(Object.is(expand(0, signed), 0), 'expand(0, mouthForm) is +0 not -0');
ok(Object.is(expand(0, unsigned), 0), 'expand(0, eyes) is +0 not -0');
ok(Object.is(expand(-0, signed), 0), 'expand(-0, mouthForm) is +0 not -0');

// Endpoints hold exactly.
eq(expand(1, signed), 1, 'expand(1, mouthForm) === 1');
eq(expand(1, unsigned), 1, 'expand(1, eyes) === 1');
eq(expand(-1, signed), -1, 'expand(-1, mouthForm) === -1');

// Odd symmetry for signed curves: expand(-x) === -expand(x).
eq(expand(-0.3, signed), -expand(0.3, signed), 'mouthForm odd at 0.3');
eq(expand(-0.7, signed), -expand(0.7, signed), 'mouthForm odd at 0.7');

// Mid-range amplification: the expressive middle lifts above linear.
ok(expand(0.5, signed) > 0.5, 'expand(0.5, mouthForm) > 0.5 (amplified)');
ok(expand(0.5, unsigned) > 0.5, 'expand(0.5, eyes) > 0.5 (amplified)');

// Noise-floor compression: near/below the floor is pulled toward 0.
ok(expand(0.03, signed) < 0.03, 'expand(0.03, mouthForm) < 0.03 (compressed)');
ok(expand(0.03, unsigned) < 0.03, 'expand(0.03, eyes) < 0.03 (compressed)');

// Monotonic non-decreasing in |x|: ascending magnitudes never dip.
const samples = [0, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 1];
let prevSigned = -Infinity;
let prevUnsigned = -Infinity;
for (const x of samples) {
  const s = expand(x, signed);
  ok(s >= prevSigned, `mouthForm non-decreasing at |x|=${x}`);
  prevSigned = s;
  const u = expand(x, unsigned);
  ok(u >= prevUnsigned, `eyes non-decreasing at |x|=${x}`);
  prevUnsigned = u;
}

// Clamp beyond the modelled range.
ok(expand(2, signed) <= signed.range[1], 'expand(2, mouthForm) <= range max');
ok(expand(-2, signed) >= signed.range[0], 'expand(-2, mouthForm) >= range min');
ok(expand(2, unsigned) <= unsigned.range[1], 'expand(2, eyes) <= range max');
// Unsigned curve clamps a negative input to 0 (below its range floor).
eq(expand(-0.5, unsigned), 0, 'expand(-0.5, eyes) clamps to 0');

console.log(`expander.js: all ${assertions} assertions passed`);
process.exit(0);
