// A2F ARKit blendshape consumer on the `voiceagent` LiveKit DataChannel.
//
// Consumes frames of the shape
//   { type:'blendshapes', frame, t, arkit:{ JawOpen:0.12, ... } }
//   { done:true }
// and routes each frame to the facial controller (face params via facial.apply)
// and the mouth controller (mouth opening via mouth.ingestA2FFrame).
//
// Wire contract: the DataChannel is LOSSY and does NOT dedup. We NEVER dedupe —
// a dropped frame is simply corrected by the next one. Order is best-effort.
//
// Stream-boundary rule (DD-5 / ADR-0013): the first blendshapes frame opens the
// stream (mouth.beginA2FStream + facial handoff on apply); the stream ends on the
// FIRST of (a) an explicit {done:true} or (b) a ~250ms idle timeout. The timeout is
// the safety net for a {done} dropped on the lossy channel, or a barge-in that
// truncates the stream mid-flight. endStream() is idempotent.
//
// Phase 3 is what actually forwards these frames from the agent over the channel.
// Until then, the dev injector (window.__a2fInject) drives the exact same consumer
// path so the pipeline is demoable end-to-end.

const IDLE_MS = 250;

export function createBlendshapes({ facial, mouth, log }) {
  let active = false;
  let idleTimer = null;

  function handleFrame(evt) {
    if (!active) {
      active = true;
      mouth.beginA2FStream();
      if (log) log('a2f stream start');
    }
    facial.apply(evt.arkit);
    mouth.ingestA2FFrame(evt);
    // Reset the idle safety net: a dropped {done} or a barge-in truncation still
    // releases the face after IDLE_MS of silence.
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(endStream, IDLE_MS);
  }

  function endStream() {
    if (idleTimer) { clearTimeout(idleTimer); idleTimer = null; }
    if (!active) return;              // idempotent
    active = false;
    facial.release();
    mouth.endA2FStream();
    if (log) log('a2f stream end');
  }

  function handle(evt) {
    if (evt.type === 'blendshapes') handleFrame(evt);
    else if (evt.done) endStream();
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

  return { wire, inject, endStream };
}
