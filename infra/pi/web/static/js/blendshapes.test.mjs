// Zero-dependency Node ESM wiring test for blendshapes.js: A2F frames routed
// through the playback scheduler (schedule.js) instead of applied on arrival.
// Fake wall clock + manual rAF pump + recording stubs for facial/mouth/debug;
// every case drives the public API only (inject / audioStopped / endStream).
// Scheduler defaults under test: lagMs=100, idleMs=250, audioStopGraceMs=500.
// Run: node infra/pi/web/static/js/blendshapes.test.mjs
import assert from 'node:assert/strict';
import { createBlendshapes } from './blendshapes.js';

let assertions = 0;
function eq(actual, expected, msg) {
  assert.equal(actual, expected, msg);
  assertions++;
}
function ok(value, msg) {
  assert.ok(value, msg);
  assertions++;
}

// Fake clock + manual rAF pump shared by all cases; makeBs() resets both.
let clock = 0;
const now = () => clock;
const rafQueue = [];
const scheduleRaf = (cb) => { rafQueue.push(cb); return rafQueue.length; };
const cancelRaf = (id) => { rafQueue[id - 1] = null; };
// Drain a snapshot of the queue: each pending cb runs exactly once per pump;
// a cb re-enqueueing itself (the module's tick loop) waits for the next pump.
function pump() {
  const len = rafQueue.length;
  for (let i = 0; i < len; i++) {
    const cb = rafQueue[i];
    rafQueue[i] = null;
    if (cb) cb();
  }
}

// Recording stubs: every sink-facing call lands in one ordered `calls` list.
function makeBs() {
  clock = 0;
  rafQueue.length = 0;
  const calls = [];
  const facial = {
    apply: (a) => calls.push(['facial.apply', a]),
    release: () => calls.push(['facial.release']),
  };
  const mouth = {
    beginA2FStream: () => calls.push(['mouth.beginA2FStream']),
    ingestA2FFrame: (f) => calls.push(['mouth.ingestA2FFrame', f]),
    endA2FStream: () => calls.push(['mouth.endA2FStream']),
  };
  const debug = {
    onStart: () => calls.push(['debug.onStart']),
    onFrame: (f) => calls.push(['debug.onFrame', f]),
    onEnd: () => calls.push(['debug.onEnd']),
  };
  const bs = createBlendshapes({ facial, mouth, log: null, debug, now, scheduleRaf, cancelRaf });
  return { bs, calls };
}

const names = (calls) => calls.map((c) => c[0]);
const count = (calls, name) => calls.filter((c) => c[0] === name).length;

// (1) inject() buffers; the rAF pump applies at the scheduled time (anchor +
// t*1000 + lag), driving debug + facial + mouth together per frame, in the
// sink order: onReplyStart = begin + debug.onStart; apply = debug.onFrame ->
// facial.apply -> mouth.ingestA2FFrame. Then (2) a second frame drains at its
// own time and {done:true} + playout ends the reply exactly once.
{
  const { bs, calls } = makeBs();
  const f1 = { type: 'blendshapes', frame: 0, t: 0, arkit: { JawOpen: 1 } };
  bs.inject(f1);                                   // anchor = 0; playout at 100
  eq(calls.length, 0, '1: nothing applied on arrival');
  clock = 50; pump();
  eq(calls.length, 0, '1: nothing applied before anchor+lag');
  clock = 100; pump();
  eq(names(calls).join(','),
    'mouth.beginA2FStream,debug.onStart,debug.onFrame,facial.apply,mouth.ingestA2FFrame',
    '1: stream open then apply, in sink order (debug at apply time)');
  ok(calls[2][1] === f1, '1: debug.onFrame got the injected frame object');
  ok(calls[3][1] === f1.arkit, '1: facial.apply got the SAME arkit object');
  ok(calls[4][1] === f1, '1: mouth.ingestA2FFrame got the SAME frame object');

  // (2) Second frame of the same reply: buffered until its playout time.
  clock = 450;                                     // arrival gap < idleMs from playout
  const f2 = { type: 'blendshapes', frame: 15, t: 0.5, arkit: { JawOpen: 0.4 } };
  bs.inject(f2);                                   // playout at anchor+500+100 = 600
  clock = 500; pump();
  eq(count(calls, 'facial.apply'), 1, '2: second frame still buffered before its time');
  clock = 600; pump();
  eq(count(calls, 'facial.apply'), 2, '2: second frame applied at its scheduled time');
  ok(calls[calls.length - 2][1] === f2.arkit && calls[calls.length - 1][1] === f2,
    '2: facial and mouth got the same second frame');
  eq(count(calls, 'mouth.beginA2FStream'), 1, '2: one stream open across both frames');
  eq(count(calls, 'facial.release'), 0, '2: no teardown before done');
  bs.inject({ done: true });                       // all frames delivered
  clock = 700; pump();                             // past last playout (600)
  eq(names(calls).slice(-3).join(','), 'facial.release,mouth.endA2FStream,debug.onEnd',
    '2: done + drained + played out -> single teardown, release before endA2FStream');
  eq(count(calls, 'debug.onEnd'), 1, '2: teardown fired exactly once');
  const n = calls.length;
  clock = 1200; pump();
  eq(calls.length, n, '2: loop parked after teardown, no further sink calls');
}

// (3) audioStopped() caps a stale tail: frames far beyond the (stopped) audio
// stay buffered; past the grace they are discarded and the reply torn down.
{
  const { bs, calls } = makeBs();
  for (let k = 0; k <= 4; k++) {
    bs.inject({ type: 'blendshapes', frame: k * 15, t: k * 0.5, arkit: { JawOpen: 0.5 } });
  }                                                // t=0..2.0, playout end 2100
  clock = 150; pump();                             // only t=0 due so far
  eq(count(calls, 'facial.apply'), 1, '3: early frames applied before the stop');
  bs.audioStopped();                               // deadline = 150 + 500 = 650
  clock = 650; pump();                             // t=0.5 (due 600) drains, then cap
  eq(count(calls, 'facial.apply'), 2, '3: NOT all frames applied — stale tail discarded');
  eq(names(calls).slice(-3).join(','), 'facial.release,mouth.endA2FStream,debug.onEnd',
    '3: grace-capped teardown fired');
  eq(count(calls, 'facial.release'), 1, '3: teardown fired exactly once');
  const n = calls.length;
  clock = 3000; pump();
  eq(calls.length, n, '3: idle after the cap, no further sink calls');
}

// (4) endStream() tears down immediately (no clock advance) and cancels the
// pending rAF; subsequent pumps drive nothing.
{
  const { bs, calls } = makeBs();
  for (let k = 0; k <= 2; k++) {
    bs.inject({ type: 'blendshapes', frame: k * 15, t: k * 0.5, arkit: { JawOpen: 0.5 } });
  }                                                // t=0..1.0
  clock = 150; pump();                             // t=0 applied -> face started
  eq(count(calls, 'facial.apply'), 1, '4: reply live before endStream');
  bs.endStream();                                  // synchronous, clock unchanged
  eq(count(calls, 'facial.release'), 1, '4: immediate facial.release');
  eq(count(calls, 'mouth.endA2FStream'), 1, '4: immediate mouth.endA2FStream');
  eq(count(calls, 'debug.onEnd'), 1, '4: immediate debug.onEnd');
  ok(rafQueue.every((e) => e === null), '4: pending rAF cancelled');
  const n = calls.length;
  clock = 5000; pump();
  eq(calls.length, n, '4: scheduler inactive — no sink calls on later pumps');
  bs.endStream();                                  // idempotent
  eq(count(calls, 'facial.release'), 1, '4: endStream idempotent, no double teardown');
}

// (5) window.__a2fInject: with a window global present at create time, the dev
// injector is installed and routes through the exact same consumer path.
{
  globalThis.window = {};
  const { bs, calls } = makeBs();
  eq(typeof globalThis.window.__a2fInject, 'function', '5: injector installed on window');
  eq(globalThis.window.__a2fInject, bs.inject, '5: injector IS the public inject()');
  globalThis.window.__a2fInject({ type: 'blendshapes', frame: 0, t: 0, arkit: { JawOpen: 1 } });
  clock = 100; pump();
  eq(count(calls, 'facial.apply'), 1, '5: injected frame plays through the scheduler');
  bs.endStream();
  delete globalThis.window;
}

console.log(`blendshapes.js: all ${assertions} assertions passed`);
process.exit(0);
