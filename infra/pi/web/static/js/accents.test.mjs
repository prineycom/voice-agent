// Zero-dependency Node ESM test for the discrete "vtuber accent" state machine
// in accents.js. Behavioral only: drives step() over a FAKE injectable clock and
// asserts observable outputs (never envelope internals). Deterministic — no real
// timers, no flake.
// Run: node infra/pi/web/static/js/accents.test.mjs
import assert from 'node:assert/strict';
import { createAccents } from './accents.js';

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

// Every step() output is recorded here so the no-mouth-open invariant can be
// checked across the whole suite at the end.
const allOutputs = [];
const isEmpty = o => Object.keys(o).length === 0;

// Build an accents machine wired to a mutable fake clock. run(frame, n, dt)
// advances the clock by dt ms per frame (~30fps default), runs n frames,
// records each output, and returns the last one.
function machine() {
  let clock = 0;
  const a = createAccents({ now: () => clock });
  function run(frame, n = 1, dt = 33) {
    let out;
    for (let i = 0; i < n; i++) {
      clock += dt;
      out = a.step(frame);
      allOutputs.push(out);
    }
    return out;
  }
  return { a, run };
}

const JOY = { MouthSmileLeft: 0.9, MouthSmileRight: 0.9 };
const smile = v => ({ MouthSmileLeft: v, MouthSmileRight: v });

// 1. Idle — an empty frame produces no keys at all.
{
  const m = machine();
  ok(isEmpty(m.run({})), 'idle step({}) -> {}');
}

// 2. Sustain gate — a held joy signal below sustainMs stays latched off ({}),
// then past sustainMs the params appear and the envelope ramps past 0.5.
{
  const m = machine();
  // ~99ms of dwell (< 250ms sustain) — the latch must not have fired yet.
  const early = m.run(JOY, 3);
  ok(isEmpty(early), 'joy held < sustainMs -> still {} (latch not fired)');
  // Keep holding well past the sustain window — the accent engages and ramps.
  const engaged = m.run(JOY, 12);
  ok('ParamEyeLSmile' in engaged, 'joy held past sustainMs -> ParamEyeLSmile appears');
  ok(engaged.ParamEyeLSmile > 0.5, 'envelope ramps ParamEyeLSmile past 0.5');
}

// 3. Schmitt no-flicker — with joy-squint active, alternating the signal below
// `on` (0.6) but above `off` (0.4) must NOT drop the accent.
{
  const m = machine();
  m.run(JOY, 15); // engage joy-squint
  const chatter = [0.5, 0.58, 0.45, 0.52, 0.47, 0.55];
  let stayed = true;
  for (const v of chatter) {
    if (!('ParamEyeLSmile' in m.run(smile(v)))) stayed = false;
  }
  ok(stayed, 'Schmitt hysteresis: signal between off/on keeps accent active (no flicker)');
}

// 4. Deactivate + omit — drop below `off` and decay: the keys joy-squint writes
// are ABSENT from the output (omitted), never written as 0.
{
  const m = machine();
  m.run(JOY, 15);
  ok('ParamEyeLSmile' in m.run(JOY), 'joy-squint active before release');
  const off = m.run({}, 40); // long enough for the envelope to fall below EPS
  ok(!('ParamEyeLSmile' in off), 'released accent OMITS ParamEyeLSmile (not 0)');
  ok(!('ParamEyeRSmile' in off), 'released accent OMITS ParamEyeRSmile');
}

// 5. Wide-eyes floor — amazement opens the eye past base 1 when active, and a
// partial ramp-up envelope must never dip the eye below its base of 1.
{
  const m = machine();
  const AMAZE = { EyeWideLeft: 0.9 };
  let firstActive = null;
  for (let i = 0; i < 20; i++) {
    const o = m.run(AMAZE);
    if (firstActive === null && 'ParamEyeLOpen' in o) firstActive = o.ParamEyeLOpen;
  }
  ok(firstActive !== null, 'amazement engages ParamEyeLOpen');
  ok(firstActive >= 1, 'ramp-up ParamEyeLOpen never dips below base 1');
  const full = m.run(AMAZE, 10);
  ok(full.ParamEyeLOpen > 1, 'active amazement opens ParamEyeLOpen past base 1');
}

// 6. Head-tilt ease-to-zero — needs the longer ~600ms sustain. On release
// ParamAngleZ magnitude strictly decreases frame-to-frame, then the key is
// omitted once small.
{
  const m = machine();
  m.run(JOY, 40); // > 600ms sustain: engage head-tilt (and joy-squint)
  const active = m.run(JOY, 10);
  ok('ParamAngleZ' in active, 'sustained joy -> head-tilt ParamAngleZ present');
  ok(active.ParamAngleZ > 8, 'ParamAngleZ eases near its target ~9');
  // Release: capture successive present values, then confirm eventual omission.
  const seq = [];
  for (let i = 0; i < 60; i++) {
    const o = m.run({});
    if ('ParamAngleZ' in o) seq.push(o.ParamAngleZ);
    else break;
  }
  ok(seq.length > 1, 'head-tilt decays over several frames before dropping');
  let decreasing = true;
  for (let i = 1; i < seq.length; i++) {
    if (!(seq[i] < seq[i - 1])) decreasing = false;
  }
  ok(decreasing, 'ParamAngleZ magnitude strictly decreasing on ease-to-zero release');
  ok(!('ParamAngleZ' in m.run({}, 5)), 'ParamAngleZ eventually OMITTED once small');
}

// 7. reset() — clears all state; a following empty step yields {} and drops the
// ease-to-zero head-tilt key immediately.
{
  const m = machine();
  const engaged = m.run(JOY, 40);
  ok('ParamAngleZ' in engaged, 'head-tilt active before reset');
  m.a.reset();
  ok(isEmpty(m.run({})), 'reset() clears state -> step({}) returns {}');
}

// 8. Accent isolation — joy-squint and amazement engage independently from one
// frame carrying both signals.
{
  const m = machine();
  const o = m.run({ MouthSmileLeft: 0.9, MouthSmileRight: 0.9, EyeWideLeft: 0.9 }, 30);
  ok('ParamEyeLSmile' in o, 'joy-squint active alongside amazement');
  ok('ParamEyeLOpen' in o, 'amazement active alongside joy-squint');
}

// 9. No mouth-open invariant — mouth *opening* is owned by the lip-sync
// provider; accents must never emit ParamMouthOpenY or JawOpen anywhere.
{
  const leaked = allOutputs.some(o => 'ParamMouthOpenY' in o || 'JawOpen' in o);
  ok(!leaked, 'never emits ParamMouthOpenY / JawOpen in any output');
}

console.log(`accents.js: all ${assertions} assertions passed`);
process.exit(0);
