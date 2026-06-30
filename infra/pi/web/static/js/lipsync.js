// Volume-based lip-sync: analyses the agent's WebRTC audio track and drives an
// avatar's mouth via avatar.setMouthOpen(0..1). Holds its own AudioContext / RAF
// handle. The track already plays via the LiveKit <audio> element, so we connect
// the source to the analyser ONLY — never to audioCtx.destination (that would
// double the audio). Optional `log` mirrors vu.js's failure logging verbatim.
// Map an RMS amplitude (0..~0.2 for speech) to a 0..1 mouth-open value.
// Gain 4 scales the small RMS up to a usable range; the 0.05 noise gate snaps
// quiet frames to a fully-closed mouth so silence reads as closed, not twitchy.
export function computeMouthTarget(rms) {
  const t = Math.min(1, Math.max(0, rms * 4));
  return t < 0.05 ? 0 : t;
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
      const tick = () => {
        analyser.getFloatTimeDomainData(buf);
        let sum = 0;
        for (const v of buf) sum += v * v;
        const rms = Math.sqrt(sum / buf.length);
        const target = computeMouthTarget(rms);
        // Lerp toward the target to kill per-frame jitter (tuned for ~60fps).
        cur += (target - cur) * 0.5;
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
