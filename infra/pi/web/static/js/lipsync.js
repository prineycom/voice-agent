// Volume-based lip-sync: analyses the agent's WebRTC audio track and drives an
// avatar's mouth via avatar.setMouthOpen(0..1). Holds its own AudioContext / RAF
// handle. The track already plays via the LiveKit <audio> element, so we connect
// the source to the analyser ONLY — never to audioCtx.destination (that would
// double the audio). Optional `log` mirrors vu.js's failure logging verbatim.
// Map an RMS amplitude to a 0..1 mouth-open value with automatic gain control.
//
// A fixed gain (the old `rms * 4`) tied the mouth opening to the absolute signal
// level, so a quieter agent / WebRTC track barely cracked the mouth open. Instead
// we normalise each frame against a *running peak* of recent speech: the loudest
// syllables open the mouth near-fully regardless of overall level. The peak decays
// toward `floorPeak` so the mapper re-sensitises after a loud passage and the floor
// stops faint background noise from reading as full-volume speech.
//
// - `gate`: absolute silence/noise floor — below it the mouth snaps fully closed.
// - `curve` < 1: perceptual expansion so mid-level speech opens the mouth wide.
// - `decayPerSec`: how fast the running peak falls back toward `floorPeak`.
// Stateful, so it's a factory; pass the frame delta `dt` for frame-rate-independent decay.
export function makeMouthMapper({ gate = 0.015, curve = 0.55, decayPerSec = 0.5, floorPeak = 0.08 } = {}) {
  let peak = floorPeak;
  return function computeMouthTarget(rms, dt = 1 / 60) {
    // Decay the peak every frame (even during silence) so it tracks the current level.
    peak = Math.max(floorPeak, peak - decayPerSec * dt * peak);
    if (rms < gate) return 0;
    peak = Math.max(peak, rms);            // instant attack: latch onto the loudest syllable
    const norm = rms / peak;               // 0..1 relative to recent speech, level-independent
    return Math.min(1, Math.max(0, Math.pow(norm, curve)));
  };
}

export function createLipSync(avatar, { log } = {}) {
  let audioCtx = null;
  let raf = null;
  let cur = 0;
  let sink = null;   // muted <audio> that keeps the remote stream flowing (see below)

  function start(mediaStreamTrack) {
    // Idempotent: tear down any prior stream first so a second TrackSubscribed
    // (e.g. the agent republishing audio) can't leak an AudioContext.
    if (audioCtx || raf) stop();
    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      // Autoplay policy can hand back a suspended context; resume it so the
      // analyser reads real audio (the Connect click is the activating gesture).
      audioCtx.resume().catch(() => {});
      const stream = new MediaStream([mediaStreamTrack]);
      // Chromium/WebKit bug: a MediaStreamAudioSourceNode fed by a *remote* WebRTC
      // track emits pure silence (analyser reads all-zeros, mouth never opens)
      // unless the same stream is also sunk into a media element. The agent audio
      // is audible via LiveKit's own <audio> element, but that uses a different
      // MediaStream; our wrapper stream needs its own sink. Mute it so we don't
      // double the audio, keep the reference alive so it isn't GC'd.
      sink = new Audio();
      sink.muted = true;
      sink.srcObject = stream;
      sink.play().catch(() => {});
      const src = audioCtx.createMediaStreamSource(stream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 512;
      analyser.smoothingTimeConstant = 0.2;
      src.connect(analyser);
      const buf = new Float32Array(analyser.fftSize);
      const computeMouthTarget = makeMouthMapper();
      const tick = () => {
        analyser.getFloatTimeDomainData(buf);
        let sum = 0;
        for (const v of buf) sum += v * v;
        const rms = Math.sqrt(sum / buf.length);
        const target = computeMouthTarget(rms);
        // Lerp toward the target to kill per-frame jitter (tuned for ~60fps).
        cur += (target - cur) * 0.6;
        avatar.setMouthOpen(cur);
        raf = requestAnimationFrame(tick);
      };
      tick();
    } catch (e) {
      // Close the partially-built context so a mid-setup failure can't leak it.
      if (audioCtx) { audioCtx.close().catch(() => {}); audioCtx = null; }
      if (sink) { sink.pause(); sink.srcObject = null; sink = null; }
      if (log) log('lip-sync недоступен: ' + e.message);
    }
  }

  function stop() {
    if (raf) cancelAnimationFrame(raf);
    raf = null;
    if (audioCtx) { audioCtx.close().catch(() => {}); audioCtx = null; }
    if (sink) { sink.pause(); sink.srcObject = null; sink = null; }
    cur = 0;
    avatar.setMouthOpen(0);
  }

  return { start, stop };
}
