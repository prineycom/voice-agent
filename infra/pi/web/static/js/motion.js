// Motion policy: maps an agent state to a Live2D motion on the injected avatar.
// No DOM, no globals — pure state -> motion mapping with debounce on identical
// states. The state->motion table is model-specific, so it comes from the active
// avatar profile (passed in for testability, defaulting to the configured active
// avatar).
//
// Per ADR-0012 the frontend no longer drives `.exp3.json` expressions (Hiyori
// ships none); A2F now drives the face (Epic 8), so the emotion->expression path
// is gone and only the motion-state half remains here.
import { activeProfile } from './avatar-config.js';

export function createMotionController(avatar, profile = activeProfile) {
  const table = profile.motions;

  function resolve(state) {
    const key = String(state).toLowerCase();
    if (key === 'listening' || key === 'thinking' || key === 'speaking') return key;
    return 'idle';
  }

  let current = null;
  let motionEventActive = false;

  function setState(state) {
    if (motionEventActive) return;
    const key = resolve(state);
    if (key === current) return;
    current = key;
    const entry = table[key];
    avatar.playMotion(entry.group, entry.index);
  }

  // Authoritative agent-published motion event: takes precedence over the
  // lk.agent.state-derived motion. The motion only restarts on an actual state
  // change — the agent emits a fresh event per inline emotion tag during one
  // reply, so restarting the same motion every time would stutter the animation
  // back to frame 0. The emotion field is ignored here (A2F drives the face).
  function applyMotionEvent(evt) {
    const key = resolve(evt && evt.state);
    const entry = table[key];
    if (key !== current) {
      current = key;
      avatar.playMotion(entry.group, entry.index);
    }
    motionEventActive = true;
  }

  return { setState, applyMotionEvent };
}
