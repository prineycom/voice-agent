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

// The #39 A2F calibration captures are emotion-INVERTED at the source: run-joy
// reads angry (brows down 0.44, mouth-frown) and run-anger reads calm (brows up,
// barely frowning). Map each demo label to the fixture that actually matches it
// so the demo is intuitive. This is a data workaround — real emotion fidelity is
// the deferred "flat A2E source" track, not a rendering fix.
const FIXTURE = { joy: 'anger', anger: 'joy' };

export function startFaceDemo(label, { log } = {}) {
  const say = (m) => { if (log) log(m); };

  // Track every scheduled timer so the demo is cancellable: a live stream must
  // be able to reclaim __a2fInject (both replay and stream feed the same
  // consumer, so they fight over the jaw/morphs otherwise).
  const timers = [];
  let stopped = false;
  const handle = {
    stop() {
      if (stopped) return;
      stopped = true;
      for (const id of timers) clearTimeout(id);
      timers.length = 0;
    },
  };

  if (label !== 'joy' && label !== 'anger') { say('демо лица: неизвестная метка ' + label); return handle; }
  if (typeof window === 'undefined' || typeof window.__a2fInject !== 'function') {
    say('демо лица: __a2fInject недоступен');
    return handle;
  }
  const inject = window.__a2fInject;

  fetch('/static/demo/run-' + FIXTURE[label] + '.json')
    .then((r) => {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .then((data) => {
      if (stopped) return;
      const frames = (data && Array.isArray(data.frames)) ? data.frames : [];
      if (!frames.length) { say('демо лица: нет кадров в ' + label); return; }
      say('старт демо лица: ' + label + ' (' + frames.length + ' кадров)');
      let lastMs = 0;
      for (const frame of frames) {
        const ms = (typeof frame.t === 'number' && frame.t > 0) ? frame.t * 1000 : 0;
        if (ms > lastMs) lastMs = ms;
        timers.push(setTimeout(() => { if (!stopped) inject(frame); }, ms));
      }
      timers.push(setTimeout(() => {
        if (stopped) return;
        inject({ done: true });
        say('конец демо лица: ' + label);
      }, lastMs + 1));
    })
    .catch((e) => say('демо лица: не удалось загрузить ' + label + ' — ' + e.message));

  return handle;
}
