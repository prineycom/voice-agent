// Volume-based lip-sync: analyses the agent's WebRTC audio track and drives an
// avatar's mouth via avatar.setMouthOpen(0..1). Holds its own AudioContext / RAF
// handle. The track already plays via the LiveKit <audio> element, so we connect
// the source to the analyser ONLY — never to audioCtx.destination (that would
// double the audio). Optional `log` mirrors vu.js's failure logging verbatim.
export function computeMouthTarget(rms) {
  const t = Math.min(1, Math.max(0, rms * 4));
  return t < 0.05 ? 0 : t;
}

export function createLipSync(avatar, { log } = {}) {
  let audioCtx = null;
  let raf = null;
  let cur = 0;

  function start(mediaStreamTrack) {
    // Idempotent: tear down any prior stream first so a second TrackSubscribed
    // (e.g. the agent republishing audio) can't leak an AudioContext.
    if (audioCtx || raf) stop();
    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const src = audioCtx.createMediaStreamSource(new MediaStream([mediaStreamTrack]));
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 512;
      analyser.smoothingTimeConstant = 0.2;
      src.connect(analyser);
      const buf = new Float32Array(analyser.fftSize);
      const tick = () => {
        analyser.getFloatTimeDomainData(buf);
        let sum = 0;
        for (const v of buf) sum += v * v;
        const rms = Math.sqrt(sum / buf.length);
        const target = computeMouthTarget(rms);
        cur += (target - cur) * 0.5;
        avatar.setMouthOpen(cur);
        raf = requestAnimationFrame(tick);
      };
      tick();
    } catch (e) {
      if (log) log('lip-sync недоступен: ' + e.message);
    }
  }

  function stop() {
    if (raf) cancelAnimationFrame(raf);
    raf = null;
    if (audioCtx) { audioCtx.close().catch(() => {}); audioCtx = null; }
    cur = 0;
    avatar.setMouthOpen(0);
  }

  return { start, stop };
}
