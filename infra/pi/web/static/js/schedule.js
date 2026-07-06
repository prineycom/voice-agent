// Playback scheduler for A2F blendshape frames. Frames arrive in bursts far
// faster than real time with a reply-relative PTS `t` (seconds); this module
// buffers them and the caller's render tick drains each frame at its scheduled
// wall time, so the face plays at real speed instead of flashing through the
// reply. Pure buffer/clock/drain — no DOM, no rAF; the caller owns the loop.
//
// Anchor policy (first-frame-of-burst): `anchorWall = now() − t·1000`, so the
// face trails the voice by A2F latency + lagMs as a bounded constant lag
// (ADR-0013 playout-aligned buffering). A `t` jump backwards (> resetEpsMs)
// means a genuinely new reply (e.g. a dropped done) → tear down and re-anchor.
// Re-anchoring is per-burst self-correcting: after an idle teardown mid-reply,
// the next sentence burst re-anchors to now, so no freeze and no wait.
//
// Teardown triggers (in tick()):
//   1. Barge-in cap    — done arrived but frames remain past doneDeadline:
//                        discard the stale tail (audio stopped; cap the face
//                        over-run to doneGraceMs).
//   2. Normal end      — done arrived, buffer drained, last frame played out.
//   3. Dropped-done net — no done, buffer drained, played out, and no frame
//                        arrived for idleMs.

export function createScheduler({ now, sink, lagMs = 100, idleMs = 250, doneGraceMs = 250, resetEpsMs = 500 } = {}) {
  now = now || (() => performance.now());

  let anchorWall = null;      // wall ms where reply t=0 plays; null = idle
  let buffer = [];            // pending frames, insertion-sorted by t
  let lastTms = 0;            // max scheduled t seen (ms), for played-out checks
  let replyDone = false;      // reply-level {done} received
  let faceStarted = false;    // sink.onReplyStart() fired for this reply
  let lastArrivalWall = 0;    // wall ms of the last push, for the idle net
  let doneDeadline = null;    // wall ms cap on draining after done (barge-in)

  function teardown() {
    if (faceStarted) sink.onReplyEnd();
    anchorWall = null;
    buffer = [];
    lastTms = 0;
    replyDone = false;
    faceStarted = false;
    doneDeadline = null;
  }

  function push(evt) {
    lastArrivalWall = now();
    // New reply: no anchor yet, or t jumped backwards (fresh reply after a
    // dropped done — small out-of-order jitter stays under resetEpsMs).
    if (anchorWall === null || evt.t * 1000 < lastTms - resetEpsMs) {
      teardown();
      anchorWall = now() - evt.t * 1000;
    }
    // Insertion sort by t: the lossy unordered channel may deliver frames
    // slightly out of order, and the drain relies on buffer[0] being earliest.
    let i = buffer.length;
    while (i > 0 && buffer[i - 1].t > evt.t) i--;
    buffer.splice(i, 0, evt);
    lastTms = Math.max(lastTms, evt.t * 1000);
  }

  function markDone() {
    if (anchorWall === null) return;
    replyDone = true;
    doneDeadline = now() + doneGraceMs;
  }

  // Returns true while a reply is active (caller keeps its rAF), false when idle.
  function tick() {
    if (anchorWall === null) return false;
    const t = now();
    while (buffer.length && anchorWall + buffer[0].t * 1000 + lagMs <= t) {
      const frame = buffer.shift();
      if (!faceStarted) {
        faceStarted = true;
        sink.onReplyStart();
      }
      sink.apply(frame);
    }
    const playedOut = t >= anchorWall + lastTms + lagMs;
    if (replyDone && buffer.length && t >= doneDeadline) {
      teardown();                                       // barge-in cap
    } else if (replyDone && buffer.length === 0 && playedOut) {
      teardown();                                       // normal end
    } else if (!replyDone && buffer.length === 0 && playedOut && (t - lastArrivalWall) > idleMs) {
      teardown();                                       // dropped-done safety net
    }
    return anchorWall !== null;
  }

  // Synchronous teardown right now (e.g. on disconnect).
  function flush() { teardown(); }

  return { push, markDone, tick, flush, get active() { return anchorWall !== null; } };
}
