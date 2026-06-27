// Zero-dependency Node ESM test for the pure resolve/debounce logic in motion.js.
// Run: node infra/pi/web/static/js/motion.test.mjs
import assert from 'node:assert/strict';
import { createMotionController } from './motion.js';

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

// 1. thinking -> TapBody/0 then Blushing
{
  const avatar = newAvatar();
  const c = createMotionController(avatar);
  c.setState('thinking');
  eq(avatar.calls, [['motion', 'TapBody', 0], ['exp', 'Blushing']], 'thinking maps to TapBody/0 + Blushing');
}

// 2. speaking -> TapBody/2 then Smile
{
  const avatar = newAvatar();
  const c = createMotionController(avatar);
  c.setState('speaking');
  eq(avatar.calls, [['motion', 'TapBody', 2], ['exp', 'Smile']], 'speaking maps to TapBody/2 + Smile');
}

// 3. casing + unknown resolution
{
  // 'Speaking' lowercases to 'speaking'
  const a1 = newAvatar();
  createMotionController(a1).setState('Speaking');
  eq(a1.calls, [['motion', 'TapBody', 2], ['exp', 'Smile']], "'Speaking' resolves to speaking via lowercase");

  // 'INITIALIZING' is unknown -> idle
  const a2 = newAvatar();
  createMotionController(a2).setState('INITIALIZING');
  eq(a2.calls, [['motion', 'Idle', 0], ['exp', 'Normal']], "'INITIALIZING' resolves to idle");

  // null -> idle
  const a3 = newAvatar();
  createMotionController(a3).setState(null);
  eq(a3.calls, [['motion', 'Idle', 0], ['exp', 'Normal']], 'null resolves to idle');

  // undefined -> idle
  const a4 = newAvatar();
  createMotionController(a4).setState(undefined);
  eq(a4.calls, [['motion', 'Idle', 0], ['exp', 'Normal']], 'undefined resolves to idle');

  // 'THINKING' -> thinking via lowercase
  const a5 = newAvatar();
  createMotionController(a5).setState('THINKING');
  eq(a5.calls, [['motion', 'TapBody', 0], ['exp', 'Blushing']], "'THINKING' resolves to thinking via lowercase");
}

// 4. debounce: same resolved state twice -> avatar called once; different state in between re-triggers
{
  const avatar = newAvatar();
  const c = createMotionController(avatar);
  c.setState('speaking');
  c.setState('speaking');
  eq(avatar.calls, [['motion', 'TapBody', 2], ['exp', 'Smile']], 'repeated speaking triggers avatar only once');

  // different resolved state re-triggers
  c.setState('listening');
  eq(avatar.calls, [
    ['motion', 'TapBody', 2], ['exp', 'Smile'],
    ['motion', 'Idle', 1], ['exp', 'Normal'],
  ], 'a different state re-triggers the avatar');

  // back to speaking re-triggers again
  c.setState('speaking');
  eq(avatar.calls.length, 6, 'returning to speaking re-triggers the avatar');

  // distinct raw states that resolve to the same key (idle) are debounced
  const avatar2 = newAvatar();
  const c2 = createMotionController(avatar2);
  c2.setState('initializing'); // -> idle
  c2.setState('boom');         // -> idle (debounced)
  eq(avatar2.calls, [['motion', 'Idle', 0], ['exp', 'Normal']], 'distinct unknown states both resolving to idle are debounced');
}

// 5. idle mapping for an unknown state
{
  const avatar = newAvatar();
  createMotionController(avatar).setState('nonsense');
  eq(avatar.calls, [['motion', 'Idle', 0], ['exp', 'Normal']], 'unknown state maps to Idle/0 + Normal');
}

// 6. applyMotionEvent drives motion (from table) + expression (from emotion map)
{
  const avatar = newAvatar();
  const c = createMotionController(avatar);
  c.applyMotionEvent({ state: 'speaking', emotion: 'happy' });
  eq(avatar.calls, [['motion', 'TapBody', 2], ['exp', 'Smile']], 'motion event speaking/happy -> TapBody/2 + Smile');
}

// 7. applyMotionEvent state -> table motion, emotion -> mapped expression (independent of table expression)
{
  const avatar = newAvatar();
  const c = createMotionController(avatar);
  c.applyMotionEvent({ state: 'thinking', emotion: 'surprised' });
  eq(avatar.calls, [['motion', 'TapBody', 0], ['exp', 'Surprised']], 'motion event thinking/surprised -> TapBody/0 + Surprised');
}

// 8. unknown emotion falls back to Normal
{
  const avatar = newAvatar();
  const c = createMotionController(avatar);
  c.applyMotionEvent({ state: 'speaking', emotion: 'bewildered' });
  eq(avatar.calls, [['motion', 'TapBody', 2], ['exp', 'Normal']], 'unknown emotion falls back to Normal');
}

// 9. precedence: once a motion event arrives, setState is ignored
{
  const avatar = newAvatar();
  const c = createMotionController(avatar);
  c.applyMotionEvent({ state: 'speaking', emotion: 'happy' });
  c.setState('listening');
  eq(avatar.calls, [['motion', 'TapBody', 2], ['exp', 'Smile']], 'setState ignored after a motion event (precedence)');
}

console.log(`motion.js: all ${assertions} assertions passed`);
process.exit(0);
