// Zero-dependency Node ESM test for the pure computeMouthTarget mapping in lipsync.js.
// Run: node infra/pi/web/static/js/lipsync.test.mjs
import assert from 'node:assert/strict';
import { computeMouthTarget } from './lipsync.js';

let assertions = 0;
function eq(actual, expected, msg) {
  assert.equal(actual, expected, msg);
  assertions++;
}
function ok(value, msg) {
  assert.ok(value, msg);
  assertions++;
}

eq(computeMouthTarget(0), 0, 'silence -> 0');
eq(computeMouthTarget(0.005), 0, 'sub-gate (0.02) -> 0');
eq(computeMouthTarget(0.1), 0.4, 'mid value -> 0.4');
eq(computeMouthTarget(1), 1, 'large value clamps to 1');
ok(computeMouthTarget(0.02) > 0, 'just over the gate stays non-zero');
eq(computeMouthTarget(0.02), 0.08, 'just over the gate -> 0.08');

console.log(`lipsync.js: all ${assertions} assertions passed`);
process.exit(0);
