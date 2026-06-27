// VU meter: drives a bar element's width from the live peak of a mic track.
// Holds its own AudioContext / RAF handle and a muted flag (muted -> 0%).
// Optional `log` mirrors the original harness's failure logging verbatim.
export function createVuMeter(barEl, { log } = {}) {
  let muted = false;
  let audioCtx = null;
  let vuRAF = null;

  function start(mediaStreamTrack) {
    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const src = audioCtx.createMediaStreamSource(new MediaStream([mediaStreamTrack]));
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 512;
      src.connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);
      const tick = () => {
        analyser.getByteTimeDomainData(data);
        let peak = 0;
        for (const v of data) peak = Math.max(peak, Math.abs(v - 128));
        const pct = muted ? 0 : Math.min(100, Math.round((peak / 128) * 180));
        barEl.style.width = pct + '%';
        vuRAF = requestAnimationFrame(tick);
      };
      tick();
    } catch (e) {
      if (log) log('VU meter недоступен: ' + e.message);
    }
  }

  function stop() {
    if (vuRAF) cancelAnimationFrame(vuRAF);
    if (audioCtx) { audioCtx.close().catch(()=>{}); audioCtx = null; }
  }

  function setMuted(value) { muted = value; }

  return { start, stop, setMuted };
}
