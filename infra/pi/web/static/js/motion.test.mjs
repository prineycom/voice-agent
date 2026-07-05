// Zero-dependency Node ESM test for the pure resolve/debounce logic in motion.js.
// Run: node infra/pi/web/static/js/motion.test.mjs
//
// The controller reads its motion map from an avatar profile. Tests pass a
// profile explicitly so they stay pinned regardless of which avatar is currently
// active. The `natori` block also guards the recoverable old avatar. Per ADR-0012
// the frontend no longer drives `.exp3.json` expressions, so only motion-state
// behaviour is asserted here.
import assert from 'node:assert/strict';
import { createMotionController } from './motion.js';
import { AVATAR_PROFILES } from './avatar-config.js';

const NATORI = AVATAR_PROFILES.natori;
const HIYORI = AVATAR_PROFILES.hiyori;

let assertions = 0;
function eq(actual, expected, msg) {
  assert.deepEqual(actual, expected, msg);
  assertions++;
}

function newAvatar() {
  return {
    ready: true,
    calls: [],
    playMotion(g, i) { this.calls.push(['motion', g, i]); },
    setExpression(n) { this.calls.push(['exp', n]); },
  };
}

// ── Natori profile (recoverable old avatar) ─────────────────────────────────

// 1. thinking -> TapBody/0
{
  const avatar = newAvatar();
  const c = createMotionController(avatar, NATORI);
  c.setState('thinking');
  eq(avatar.calls, [['motion', 'TapBody', 0]], 'thinking maps to TapBody/0');
}

// 2. speaking -> TapBody/2
{
  const avatar = newAvatar();
  const c = createMotionController(avatar, NATORI);
  c.setState('speaking');
  eq(avatar.calls, [['motion', 'TapBody', 2]], 'speaking maps to TapBody/2');
}

// 3. casing + unknown resolution
{
  // 'Speaking' lowercases to 'speaking'
  const a1 = newAvatar();
  createMotionController(a1, NATORI).setState('Speaking');
  eq(a1.calls, [['motion', 'TapBody', 2]], "'Speaking' resolves to speaking via lowercase");

  // 'INITIALIZING' is unknown -> idle
  const a2 = newAvatar();
  createMotionController(a2, NATORI).setState('INITIALIZING');
  eq(a2.calls, [['motion', 'Idle', 0]], "'INITIALIZING' resolves to idle");

  // null -> idle
  const a3 = newAvatar();
  createMotionController(a3, NATORI).setState(null);
  eq(a3.calls, [['motion', 'Idle', 0]], 'null resolves to idle');

  // undefined -> idle
  const a4 = newAvatar();
  createMotionController(a4, NATORI).setState(undefined);
  eq(a4.calls, [['motion', 'Idle', 0]], 'undefined resolves to idle');

  // 'THINKING' -> thinking via lowercase
  const a5 = newAvatar();
  createMotionController(a5, NATORI).setState('THINKING');
  eq(a5.calls, [['motion', 'TapBody', 0]], "'THINKING' resolves to thinking via lowercase");
}

// 4. debounce: same resolved state twice -> avatar called once; different state in between re-triggers
{
  const avatar = newAvatar();
  const c = createMotionController(avatar, NATORI);
  c.setState('speaking');
  c.setState('speaking');
  eq(avatar.calls, [['motion', 'TapBody', 2]], 'repeated speaking triggers avatar only once');

  // different resolved state re-triggers
  c.setState('listening');
  eq(avatar.calls, [
    ['motion', 'TapBody', 2],
    ['motion', 'Idle', 1],
  ], 'a different state re-triggers the avatar');

  // back to speaking re-triggers again
  c.setState('speaking');
  eq(avatar.calls.length, 3, 'returning to speaking re-triggers the avatar');

  // distinct raw states that resolve to the same key (idle) are debounced
  const avatar2 = newAvatar();
  const c2 = createMotionController(avatar2, NATORI);
  c2.setState('initializing'); // -> idle
  c2.setState('boom');         // -> idle (debounced)
  eq(avatar2.calls, [['motion', 'Idle', 0]], 'distinct unknown states both resolving to idle are debounced');
}

// 5. idle mapping for an unknown state
{
  const avatar = newAvatar();
  createMotionController(avatar, NATORI).setState('nonsense');
  eq(avatar.calls, [['motion', 'Idle', 0]], 'unknown state maps to Idle/0');
}

// 6. applyMotionEvent drives motion (from table)
{
  const avatar = newAvatar();
  const c = createMotionController(avatar, NATORI);
  c.applyMotionEvent({ state: 'speaking', emotion: 'happy' });
  eq(avatar.calls, [['motion', 'TapBody', 2]], 'motion event speaking -> TapBody/2');
}

// 7. applyMotionEvent state -> table motion (emotion ignored)
{
  const avatar = newAvatar();
  const c = createMotionController(avatar, NATORI);
  c.applyMotionEvent({ state: 'thinking', emotion: 'surprised' });
  eq(avatar.calls, [['motion', 'TapBody', 0]], 'motion event thinking -> TapBody/0');
}

// 9. precedence: once a motion event arrives, setState is ignored
{
  const avatar = newAvatar();
  const c = createMotionController(avatar, NATORI);
  c.applyMotionEvent({ state: 'speaking', emotion: 'happy' });
  c.setState('listening');
  eq(avatar.calls, [['motion', 'TapBody', 2]], 'setState ignored after a motion event (precedence)');
}

// 10. emotion-only change (same state, new emotion) does not restart motion
{
  const avatar = newAvatar();
  const c = createMotionController(avatar, NATORI);
  c.applyMotionEvent({ state: 'speaking', emotion: 'happy' });
  c.applyMotionEvent({ state: 'speaking', emotion: 'sad' });
  eq(avatar.calls, [['motion', 'TapBody', 2]], 'same state with a new emotion does not restart the motion');
}

// 11. an actual state change re-triggers the motion
{
  const avatar = newAvatar();
  const c = createMotionController(avatar, NATORI);
  c.applyMotionEvent({ state: 'speaking', emotion: 'happy' });
  c.applyMotionEvent({ state: 'thinking', emotion: 'happy' });
  eq(avatar.calls, [
    ['motion', 'TapBody', 2],
    ['motion', 'TapBody', 0],
  ], 'a new state restarts the motion');
}

// ── Hiyori profile (active avatar) ──────────────────────────────────────────
// Hiyori has no expression files (ADR-0012: the frontend drives no `.exp3.json`
// expressions). Motion groups: Idle (0..8) + TapBody (only index 0). speaking
// must therefore be TapBody/0, not TapBody/2.

// H1. state -> motion mapping uses valid Hiyori indices
{
  const avatar = newAvatar();
  const c = createMotionController(avatar, HIYORI);
  c.setState('speaking');
  eq(avatar.calls, [['motion', 'TapBody', 0]], 'Hiyori speaking -> TapBody/0');

  c.setState('thinking');
  eq(avatar.calls, [
    ['motion', 'TapBody', 0],
    ['motion', 'Idle', 2],
  ], 'Hiyori thinking -> Idle/2');
}

// H2. unknown state -> idle (Idle/0)
{
  const avatar = newAvatar();
  createMotionController(avatar, HIYORI).setState('whatever');
  eq(avatar.calls, [['motion', 'Idle', 0]], 'Hiyori unknown state -> Idle/0');
}

// H3. applyMotionEvent: motion from table
{
  const avatar = newAvatar();
  const c = createMotionController(avatar, HIYORI);
  c.applyMotionEvent({ state: 'speaking', emotion: 'happy' });
  eq(avatar.calls, [['motion', 'TapBody', 0]], 'Hiyori motion event -> TapBody/0');
}

console.log(`motion.js: all ${assertions} assertions passed`);
process.exit(0);
