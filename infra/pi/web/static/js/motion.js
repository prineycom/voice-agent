// Motion policy: maps an agent state to a Live2D motion + expression on the
// injected avatar. No DOM, no globals — pure state -> motion mapping with
// debounce on identical states. The state->motion table and emotion->expression
// map are model-specific, so they come from the active avatar profile (passed in
// for testability, defaulting to the configured active avatar).
import { activeProfile } from './avatar-config.js';

export function createMotionController(avatar, profile = activeProfile) {
  const table = profile.motions;

  const EMOTION_EXPR = profile.emotionExpr;
  const FALLBACK_EXPR = profile.fallbackExpr;

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
    avatar.setExpression(entry.expression);
  }

  // Authoritative agent-published motion event: takes precedence over the
  // lk.agent.state-derived motion. The expression always updates (emotion-only
  // changes), but the motion only restarts on an actual state change — the agent
  // emits a fresh event per inline emotion tag during one reply, so restarting
  // the same motion every time would stutter the animation back to frame 0.
  function applyMotionEvent(evt) {
    const key = resolve(evt && evt.state);
    const entry = table[key];
    if (key !== current) {
      current = key;
      avatar.playMotion(entry.group, entry.index);
    }
    const mapped = EMOTION_EXPR[String(evt && evt.emotion).toLowerCase()];
    avatar.setExpression(mapped == null ? FALLBACK_EXPR : mapped);
    motionEventActive = true;
  }

  return { setState, applyMotionEvent };
}
