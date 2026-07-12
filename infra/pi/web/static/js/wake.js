// Wake activation signal (#60): plays a chime + reacts the avatar + updates the
// header badge on the Dormant/Active transitions the agent publishes as
// {"type":"wake","state":"active"|"dormant"} on the `voiceagent` data channel
// (ADR-0021). Shared by both index.html (Live2D) and face3d.html (3D).
//
// The chime goes through the browser's own WebAudio output, NOT LiveKit — it is
// local UI feedback, not agent audio. WebAudio needs a user gesture to start; the
// Connect click is that gesture, and wake events only arrive after Connect, so the
// AudioContext resumes fine (we also resume() defensively on each play).
//
// The avatar reaction reuses the motion policy via applyMotionEvent so it rides
// the SAME authoritative-motion path as the agent (ADR-0009) rather than fighting
// it: on `active` the avatar perks up toward `listening` (which the agent is about
// to drive anyway as it starts processing); on `dormant` it settles to `idle`
// while the agent is silent. On the 3D page `motion` is a no-op stub (static head),
// so there the chime + badge are the signal.

// Default chime: a short two-note WebAudio blip. `active` rises and is a touch
// louder (an inviting "I'm listening"); `dormant` falls and is quieter (a discreet
// "going to sleep"). Returns a player bound to a lazily-created AudioContext.
export function makeChime({ log } = {}) {
  let audioCtx = null;

  function ensureCtx() {
    if (!audioCtx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      try {
        audioCtx = new AC();
      } catch (e) {
        if (log) log('chime недоступен: ' + e.message);
        return null;
      }
    }
    if (audioCtx.state === 'suspended') audioCtx.resume().catch(() => {});
    return audioCtx;
  }

  return function play(kind) {
    const ctx = ensureCtx();
    if (!ctx) return;
    const now = ctx.currentTime;
    const notes = kind === 'active' ? [660, 990] : [560, 370];
    const peak = kind === 'active' ? 0.18 : 0.10;
    notes.forEach((freq, i) => {
      const t = now + i * 0.09;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.linearRampToValueAtTime(peak, t + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.18);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(t);
      osc.stop(t + 0.2);
    });
  };
}

// motion: the motion controller (applyMotionEvent). agentState: the header badge
// controller (setDormant/setAwake). playChime: injectable for tests; defaults to
// the WebAudio chime above.
export function createWakeSignal({ motion, agentState, log, playChime } = {}) {
  const chime = playChime || makeChime({ log });
  let lastState = null;

  function handle(evt) {
    const state = evt && evt.state;
    if (state !== 'active' && state !== 'dormant') return;
    if (state === lastState) return; // de-dup the lossy re-sends / repeats
    lastState = state;

    chime(state);
    if (state === 'active') {
      if (motion) motion.applyMotionEvent({ state: 'listening' }); // perk up
      if (agentState) agentState.setAwake();
    } else {
      if (motion) motion.applyMotionEvent({ state: 'dormant' }); // settle → idle
      if (agentState) agentState.setDormant();
    }
    if (log) log('wake: ' + state);
  }

  // Forget the last state (on disconnect) so a reconnect re-applies the first
  // transition it sees rather than de-duping against a stale value.
  function reset() {
    lastState = null;
  }

  return { handle, reset };
}
