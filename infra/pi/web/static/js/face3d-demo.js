// Offline A2F demo replayer. Drives the 3D face from a recorded calibration
// capture (docs/research/data/39-a2f-calibration → static/demo) WITHOUT a live
// agent, by injecting each recorded frame through window.__a2fInject — the exact
// production consumer path (blendshapes.js handle → scheduler). This satisfies
// issue #46 AC#3 ("demonstrated with run-joy.json / run-anger.json").
//
// The fixtures are { meta, frames:[ { type:'blendshapes', frame, t, arkit:{..} }, ..] }
// with `t` in SECONDS (frame 0 has t:0.0). We inject each frame VERBATIM at
// t·1000 ms on a single setTimeout base, then a {done:true} after the last frame
// so the scheduler tears the stream down normally. We do NOT add/modify fields
// and we do NOT call beginA2FStream ourselves — the scheduler opens the stream on
// the first applied frame and paces by `t` on a wall-clock anchor.

export function startFaceDemo(label, { log } = {}) {
  const say = (m) => { if (log) log(m); };

  if (label !== 'joy' && label !== 'anger') { say('face demo: unknown label ' + label); return; }
  if (typeof window === 'undefined' || typeof window.__a2fInject !== 'function') {
    say('face demo: __a2fInject unavailable');
    return;
  }
  const inject = window.__a2fInject;

  fetch('/static/demo/run-' + label + '.json')
    .then((r) => {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .then((data) => {
      const frames = (data && Array.isArray(data.frames)) ? data.frames : [];
      if (!frames.length) { say('face demo: no frames in ' + label); return; }
      say('face demo start: ' + label + ' (' + frames.length + ' frames)');
      let lastMs = 0;
      for (const frame of frames) {
        const ms = (typeof frame.t === 'number' && frame.t > 0) ? frame.t * 1000 : 0;
        if (ms > lastMs) lastMs = ms;
        setTimeout(() => inject(frame), ms);
      }
      setTimeout(() => {
        inject({ done: true });
        say('face demo end: ' + label);
      }, lastMs + 1);
    })
    .catch((e) => say('face demo: failed to load ' + label + ' — ' + e.message));
}
