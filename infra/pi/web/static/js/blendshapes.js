// A2F ARKit blendshape consumer on the `voiceagent` LiveKit DataChannel.
//
// Consumes frames of the shape
//   { type:'blendshapes', frame, t, arkit:{ JawOpen:0.12, ... } }
//   { done:true }
// and routes them into the playback scheduler (schedule.js), which applies each
// frame at its reply-relative `t` on a wall-clock anchor set per burst — the face
// plays at real speed instead of flashing through the reply. At apply time each
// frame goes to the facial controller (face params via facial.apply) and the
// mouth controller (mouth opening via mouth.ingestA2FFrame); debug hooks fire at
// apply time too, so ?facedebug=1 measures playback, not arrival.
//
// Wire contract: the DataChannel is LOSSY and does NOT dedup. We NEVER dedupe —
// a dropped frame is simply corrected by the next one. Order is best-effort
// (the scheduler insertion-sorts by t).
//
// Stream-boundary rule (DD-5 / ADR-0013): the scheduler opens the stream on the
// first APPLIED frame (mouth.beginA2FStream + facial handoff) and ends it on the
// first of an explicit {done:true} played out, an audio-stopped grace cap (agent
// state leaves 'speaking' — playout end or barge-in), or an idle safety net for
// a {done} dropped on the lossy channel — see schedule.js for the timings (lag
// 100ms, idle 250ms, audio-stop grace 500ms). endStream() is idempotent.
//
// Phase 3 is what actually forwards these frames from the agent over the channel.
// Until then, the dev injector (window.__a2fInject) drives the exact same consumer
// path so the pipeline is demoable end-to-end.

import { createScheduler } from './schedule.js';

export function createBlendshapes({
  facial, mouth, log, debug,
  // Injectables for node tests; browser callers pass nothing extra.
  now = () => performance.now(),
  scheduleRaf = (cb) => (typeof window !== 'undefined' ? window.requestAnimationFrame(cb) : null),
  cancelRaf = (id) => { if (typeof window !== 'undefined') window.cancelAnimationFrame(id); },
}) {
  const sink = {
    onReplyStart() {
      mouth.beginA2FStream();
      if (debug) debug.onStart();
      if (log) log('a2f stream start');
    },
    apply(f) {
      if (debug) debug.onFrame(f);
      facial.apply(f.arkit);
      mouth.ingestA2FFrame(f);
    },
    onReplyEnd() {
      // Order matters (see room.js onDisconnected): facial.release() first, then
      // mouth.endA2FStream() clears a2fActive so volume ticks reclaim the mouth.
      facial.release();
      mouth.endA2FStream();
      if (debug) debug.onEnd();
      if (log) log('a2f stream end');
    },
  };

  const sched = createScheduler({ now, sink });

  // Drive the scheduler from rAF while a reply is active; the loop parks itself
  // when tick() reports idle. scheduleRaf may return null (non-window env) —
  // treat that as "no loop available" (tests pump tick via an injected scheduleRaf).
  let raf = null;
  function tick() {
    const active = sched.tick();
    raf = active ? scheduleRaf(tick) : null;
  }
  function ensureLoop() {
    if (raf === null) raf = scheduleRaf(tick);
  }

  function endStream() {
    sched.flush();
    if (raf !== null) { cancelRaf(raf); raf = null; }
  }

  // Called by main.js when the agent state leaves 'speaking' (audio playout
  // ended or barge-in); the loop must run so the grace-capped teardown fires
  // even if no further frames arrive.
  function audioStopped() { sched.audioStopped(); ensureLoop(); }

  function handle(evt) {
    if (evt.type === 'blendshapes') { sched.push(evt); ensureLoop(); }
    else if (evt.done) { sched.markDone(); ensureLoop(); }  // loop must run to drain/teardown
  }

  function wire(room) {
    const { RoomEvent } = window.LivekitClient;
    room.on(RoomEvent.DataReceived, (payload, _participant, _kind, topic) => {
      if (topic !== 'voiceagent') return;
      let evt;
      try { evt = JSON.parse(new TextDecoder().decode(payload)); } catch { return; }
      handle(evt);
    });
  }

  // Dev injector: route through the SAME handle() as the wire, so
  //   window.__a2fInject({ type:'blendshapes', t:.., arkit:{..} })
  //   window.__a2fInject({ done:true })
  // exercise the exact production consumer path.
  function inject(frame) { handle(frame); }
  if (typeof window !== 'undefined') window.__a2fInject = inject;

  return { wire, inject, endStream, audioStopped };
}
