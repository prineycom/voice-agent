// Zero-dependency Node ESM test for the A2F playback scheduler in schedule.js.
// Fake wall clock + stub sink; every case drives the public API only
// (push / markDone / audioStopped / tick / flush / active).
// Run: node infra/pi/web/static/js/schedule.test.mjs
import assert from 'node:assert/strict';
import { createScheduler } from './schedule.js';

let assertions = 0;
function eq(actual, expected, msg) {
  assert.equal(actual, expected, msg);
  assertions++;
}
function ok(value, msg) {
  assert.ok(value, msg);
  assertions++;
}

// Fake clock shared by all cases; each case resets it via makeSched().
let clock = 0;
const now = () => clock;

// Stub sink: records every lifecycle event with the clock it fired at.
function makeSched() {
  clock = 0;
  const log = [];
  const sink = {
    onReplyStart() { log.push({ ev: 'start', clock }); },
    apply(frame) { log.push({ ev: 'apply', t: frame.t, clock }); },
    onReplyEnd() { log.push({ ev: 'end', clock }); },
  };
  // Defaults under test: lagMs=100, idleMs=250, audioStopGraceMs=500, resetEpsMs=500.
  return { sched: createScheduler({ now, sink }), log };
}

const f = (t) => ({ type: 'blendshapes', frame: Math.round(t * 30), t, arkit: { JawOpen: 0.5 } });
const G = 33 / 1000;                       // 33ms A2F frame grid, exact in ms
const applies = (log) => log.filter((e) => e.ev === 'apply');
const count = (log, ev) => log.filter((e) => e.ev === ev).length;

// (a) Big burst played out in real time: 30 frames (t=0..0.957) arrive within
// a few ms; the drain must spread them across ~1s of wall clock, not flash
// through. Done marks delivery-complete only; here it lands near end of playout
// (an early done must not truncate either — see case e1).
{
  const { sched, log } = makeSched();
  for (let k = 0; k < 30; k++) { sched.push(f(k * G)); clock += 0.1; } // burst in ~3ms
  // anchor = 0 (first push was t=0 at clock 0); last t = 957ms, playout end = 1057.
  let maxPerTick = 0;
  let last = true;
  let doneMarked = false;
  while (clock < 1300 && last) {
    clock += 16;
    if (clock >= 960 && !doneMarked) { sched.markDone(); doneMarked = true; } // delivery complete
    const before = applies(log).length;
    last = sched.tick();
    maxPerTick = Math.max(maxPerTick, applies(log).length - before);
  }
  const a = applies(log);
  eq(a.length, 30, 'a: all 30 frames applied');
  eq(count(log, 'start'), 1, 'a: onReplyStart fired exactly once');
  eq(log[0].ev, 'start', 'a: onReplyStart precedes the first apply');
  ok(a[0].clock >= 100 && a[0].clock <= 116, `a: first apply ~anchor+lag (got ${a[0].clock})`);
  ok(a[a.length - 1].clock >= 1057, `a: last apply >= anchor+957+lag (got ${a[a.length - 1].clock})`);
  ok(maxPerTick <= 2, `a: per-tick applies bounded (got ${maxPerTick}), never a flash-through`);
  eq(count(log, 'end'), 1, 'a: onReplyEnd fired exactly once');
  ok(log[log.length - 1].ev === 'end' && log[log.length - 1].clock >= 1057,
    'a: onReplyEnd after the last frame playout time');
  eq(sched.tick(), false, 'a: tick() returns false after end');
  eq(sched.active, false, 'a: inactive after end');
}

// (b) Continuation burst (same reply, arrival gap < idleMs): one onReplyStart,
// applies continuous over both bursts, no onReplyEnd until done + drained.
{
  const { sched, log } = makeSched();
  for (let k = 0; k <= 9; k++) sched.push(f(k * G));          // t=0..0.297
  while (clock < 240) { clock += 16; sched.tick(); }          // drain some (gap stays < 250)
  ok(applies(log).length > 0 && applies(log).length < 10, 'b: mid-drain when continuation arrives');
  for (let k = 10; k <= 18; k++) sched.push(f(k * G));        // t=0.330..0.594, gap 240ms < idle
  while (clock < 600) { clock += 16; sched.tick(); }
  eq(count(log, 'start'), 1, 'b: exactly one onReplyStart across both bursts');
  eq(count(log, 'end'), 0, 'b: no onReplyEnd before done');
  sched.markDone();                                           // delivery complete; playout end 694
  while (clock < 750) { clock += 16; sched.tick(); }
  const a = applies(log);
  eq(a.length, 19, 'b: every frame of both bursts applied');
  ok(a.every((e, i) => i === 0 || e.t > a[i - 1].t), 'b: applies continuous in ascending t');
  eq(count(log, 'end'), 1, 'b: onReplyEnd once done and drained');
  ok(log[log.length - 1].clock >= 694, 'b: end waits for last-frame playout (anchor+594+lag)');
}

// (c) Sentence gap > idleMs with no done: idle net tears down, and the next
// sentence burst re-anchors to now — its first apply lands ~lagMs after the
// push, not 2.4s later.
{
  const { sched, log } = makeSched();
  for (let k = 0; k <= 15; k++) sched.push(f(k * G));         // t=0..0.495, playout end 595
  while (clock < 650) { clock += 16; sched.tick(); }          // drain, then idle > 250ms since arrival
  eq(count(log, 'end'), 1, 'c: idle net fired onReplyEnd without a done');
  eq(sched.active, false, 'c: scheduler idle after the net');
  clock = 700;
  for (let k = 0; k <= 2; k++) sched.push(f(2.4 + k * G));    // sentence 2 resumes at t=2.4
  const beforeApplies = applies(log).length;
  while (clock < 900 && applies(log).length === beforeApplies) { clock += 16; sched.tick(); }
  eq(count(log, 'start'), 2, 'c: new onReplyStart for sentence 2');
  const first2 = applies(log)[beforeApplies];
  eq(first2.t, 2.4, 'c: sentence 2 starts from its first frame');
  ok(first2.clock - 700 >= 100 && first2.clock - 700 <= 132,
    `c: re-anchor is self-correcting — first apply ~lag after push, not t=2.4s later (got +${first2.clock - 700}ms)`);
  sched.flush();
}

// (d) t-reset: a t jump backwards past resetEpsMs while a reply is live means a
// new reply (dropped done) — old reply torn down, new onReplyStart on drain.
{
  const { sched, log } = makeSched();
  sched.push(f(0));
  sched.push(f(1.0));                                         // lastT = 1000ms
  while (clock < 150) { clock += 16; sched.tick(); }          // t=0 applied -> faceStarted
  eq(count(log, 'start'), 1, 'd: reply 1 started');
  eq(count(log, 'end'), 0, 'd: reply 1 still live');
  sched.push(f(0));                                           // 0 < 1000-500 -> reset
  eq(count(log, 'end'), 1, 'd: old reply torn down on the backwards t jump');
  eq(sched.active, true, 'd: new reply anchored immediately');
  while (clock < 300) { clock += 16; sched.tick(); }
  eq(count(log, 'start'), 2, 'd: new onReplyStart on the next drain');
  sched.flush();
}

// (e1) Early done does NOT truncate: {done} is sent at synthesis-complete,
// which on a long reply lands mid-playout with seconds of correctly-scheduled
// frames still buffered — every one must still play out at its own time.
{
  const { sched, log } = makeSched();
  for (let k = 0; k <= 60; k++) sched.push(f(k * G));         // t=0..1.980, playout end 2080
  sched.markDone();                                           // synthesis done, audio still playing
  let last = true;
  while (clock < 2300 && last) { clock += 16; last = sched.tick(); }
  eq(applies(log).length, 61, 'e1: all frames applied despite the early done');
  const a = applies(log);
  ok(a[a.length - 1].clock >= 2080, `e1: last apply at its scheduled time (got ${a[a.length - 1].clock})`);
  eq(count(log, 'end'), 1, 'e1: onReplyEnd fired exactly once');
  ok(log[log.length - 1].clock >= 2080, 'e1: teardown only after the full playout');
  eq(sched.active, false, 'e1: idle after the normal end');
}

// (e2) audioStopped caps a stale tail: the agent state left `speaking`
// (barge-in) while the buffer still holds a long tail — the face over-run is
// capped at audioStopGraceMs and the stale tail is discarded, not played out.
{
  const { sched, log } = makeSched();
  for (let k = 0; k <= 60; k++) sched.push(f(k * G));         // t=0..1.980, one burst
  while (clock < 200) { clock += 16; sched.tick(); }          // only early frames applied
  const stopClock = clock;
  sched.audioStopped();                                       // deadline = stopClock + 500
  let last = true;
  while (clock < 1200 && last) { clock += 16; last = sched.tick(); }
  ok(applies(log).length < 61, `e2: stale tail discarded (${applies(log).length}/61 applied)`);
  eq(count(log, 'end'), 1, 'e2: onReplyEnd fired despite frames left in the buffer');
  const endClock = log[log.length - 1].clock;
  ok(endClock - stopClock >= 500 && endClock - stopClock <= 516,
    `e2: teardown within ~audioStopGraceMs of audioStopped (got +${endClock - stopClock}ms)`);
  eq(sched.active, false, 'e2: idle after the cap');
}

// (e3) audioStopped when idle is a no-op: agent-state transitions also fire
// between replies (and before the first one) — they must not touch anything.
{
  const { sched, log } = makeSched();
  sched.audioStopped();                                       // before any reply
  eq(sched.active, false, 'e3: still idle before any push');
  eq(sched.tick(), false, 'e3: tick() stays false');
  eq(log.length, 0, 'e3: no sink calls');
  sched.push(f(0));
  clock = 100; sched.tick();                                  // applied at anchor+lag
  sched.markDone();
  clock = 120; sched.tick();                                  // done + drained + played out
  eq(count(log, 'end'), 1, 'e3: reply completed normally');
  sched.audioStopped();                                       // after a completed reply
  clock = 700;
  eq(sched.tick(), false, 'e3: no reactivation from a late audioStopped');
  eq(count(log, 'end'), 1, 'e3: no extra sink calls after the reply ended');
}

// (e4) Normal end wins over an unexpired grace: burst delivered (done) and
// audio stops right around playout end — the tail drains fully within the
// grace and teardown is the clean normal end, exactly once (idempotent paths).
{
  const { sched, log } = makeSched();
  for (let k = 0; k <= 9; k++) sched.push(f(k * G));          // t=0..0.297, playout end 397
  sched.markDone();                                           // delivery complete
  while (clock < 200) { clock += 16; sched.tick(); }
  sched.audioStopped();                                       // deadline ~708, beyond playout end
  let last = true;
  while (clock < 800 && last) { clock += 16; last = sched.tick(); }
  eq(applies(log).length, 10, 'e4: full drain — the grace did not truncate a live tail');
  eq(count(log, 'end'), 1, 'e4: clean single onReplyEnd');
  ok(log[log.length - 1].clock >= 397 && log[log.length - 1].clock < 700,
    `e4: normal end at playout, not at the grace deadline (got ${log[log.length - 1].clock})`);
  sched.audioStopped();                                       // late state event after teardown
  eq(sched.tick(), false, 'e4: still idle');
  eq(count(log, 'end'), 1, 'e4: no double-end');
}

// (f) Idle net exactness: after drain-complete with no done, teardown must not
// fire until strictly more than idleMs has passed since the last arrival.
{
  const { sched, log } = makeSched();
  sched.push(f(0));                                           // arrival at clock 0
  clock = 100; sched.tick();                                  // applied and played out at anchor+lag
  eq(applies(log).length, 1, 'f: frame applied at playout time');
  clock = 250;                                                // exactly idleMs since arrival
  eq(sched.tick(), true, 'f: no teardown at exactly idleMs');
  eq(count(log, 'end'), 0, 'f: onReplyEnd not fired yet');
  clock = 251;                                                // just past idleMs
  eq(sched.tick(), false, 'f: teardown just past idleMs');
  eq(count(log, 'end'), 1, 'f: idle net fired onReplyEnd');
}

// (g) Lag: a frame with t=1.0 pushed at clock C anchors at C-1000, so it plays
// exactly when the clock reaches C + lagMs.
{
  const { sched, log } = makeSched();
  clock = 1000;                                               // C
  sched.push(f(1.0));                                         // anchor = 0
  clock = 1099; sched.tick();
  eq(applies(log).length, 0, 'g: not applied just below C+lag');
  clock = 1100; sched.tick();
  eq(applies(log).length, 1, 'g: applied once clock reaches C+lag');
  eq(applies(log)[0].clock, 1100, 'g: applied exactly at anchor + t*1000 + lag');
  sched.flush();
}

// (h) Out-of-order arrival: the lossy channel may reorder frames; insertion
// sort must drain them in ascending t. Pushing t=0.066 first anchors at
// now-66, so all three still play in order shortly after anchor+lag.
{
  const { sched, log } = makeSched();
  sched.push(f(0.066));                                       // anchors at -66
  sched.push(f(0.0));                                         // backwards, but < resetEps: same reply
  sched.push(f(0.033));
  eq(count(log, 'end'), 0, 'h: sub-resetEps backwards t is jitter, not a reset');
  while (clock < 200) { clock += 16; sched.tick(); }
  const ts = applies(log).map((e) => e.t);
  eq(ts.length, 3, 'h: all three frames applied');
  ok(ts.every((t, i) => i === 0 || t > ts[i - 1]), `h: applies in ascending t order (got ${ts.join(',')})`);
  sched.flush();
}

// (i) flush(): synchronous mid-reply teardown (e.g. on disconnect).
{
  const { sched, log } = makeSched();
  for (let k = 0; k <= 9; k++) sched.push(f(k * G));
  while (clock < 150) { clock += 16; sched.tick(); }          // reply started, buffer non-empty
  eq(count(log, 'start'), 1, 'i: reply live before flush');
  sched.flush();
  eq(count(log, 'end'), 1, 'i: flush fires onReplyEnd for a started reply');
  eq(sched.active, false, 'i: inactive after flush');
  eq(sched.tick(), false, 'i: tick() returns false after flush');
}

console.log(`schedule.js: all ${assertions} assertions passed`);
process.exit(0);
