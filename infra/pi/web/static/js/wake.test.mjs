// Zero-dependency Node ESM test for the wake activation signal (#60): the
// Dormant/Active dispatch in wake.js and the badge's dormant/awake behaviour in
// agent-state.js. WebAudio isn't available in Node, so the chime is injected as a
// spy (createWakeSignal accepts playChime); agent-state is driven with a fake
// badge element.
// Run: node infra/pi/web/static/js/wake.test.mjs
import assert from 'node:assert/strict';
import { createWakeSignal } from './wake.js';
import { createAgentState } from './agent-state.js';

let assertions = 0;
function eq(actual, expected, msg) {
  assert.deepEqual(actual, expected, msg);
  assertions++;
}
function ok(cond, msg) {
  assert.ok(cond, msg);
  assertions++;
}

function fakeMotion() {
  return { calls: [], applyMotionEvent(evt) { this.calls.push(evt.state); } };
}
function fakeAgentState() {
  return {
    calls: [],
    setAwake() { this.calls.push('awake'); },
    setDormant() { this.calls.push('dormant'); },
  };
}
function fakeBadge() {
  return { textContent: '', className: '' };
}

// --- createWakeSignal: active transition ------------------------------------
{
  const chimes = [];
  const motion = fakeMotion();
  const agentState = fakeAgentState();
  const wake = createWakeSignal({ motion, agentState, playChime: (k) => chimes.push(k) });

  wake.handle({ type: 'wake', state: 'active' });
  eq(chimes, ['active'], 'active plays the active chime');
  eq(motion.calls, ['listening'], 'active perks the avatar toward listening');
  eq(agentState.calls, ['awake'], 'active wakes the badge');
}

// --- createWakeSignal: dormant transition -----------------------------------
{
  const chimes = [];
  const motion = fakeMotion();
  const agentState = fakeAgentState();
  const wake = createWakeSignal({ motion, agentState, playChime: (k) => chimes.push(k) });

  wake.handle({ type: 'wake', state: 'active' });
  wake.handle({ type: 'wake', state: 'dormant' });
  eq(chimes, ['active', 'dormant'], 'dormant plays the distinct sleep chime');
  eq(motion.calls, ['listening', 'dormant'], 'dormant settles the avatar to idle');
  eq(agentState.calls, ['awake', 'dormant'], 'dormant sleeps the badge');
}

// --- de-dup repeated/lossy re-sends -----------------------------------------
{
  const chimes = [];
  const wake = createWakeSignal({ playChime: (k) => chimes.push(k) });
  wake.handle({ state: 'active' });
  wake.handle({ state: 'active' }); // repeat → ignored
  wake.handle({ state: 'active' });
  eq(chimes, ['active'], 'a repeated same-state event chimes only once');
}

// --- ignore non-wake / malformed --------------------------------------------
{
  const chimes = [];
  const wake = createWakeSignal({ playChime: (k) => chimes.push(k) });
  wake.handle({ state: 'weird' });
  wake.handle({});
  wake.handle(null);
  eq(chimes, [], 'unknown/malformed states are ignored');
}

// --- reset re-arms so a reconnect re-applies --------------------------------
{
  const chimes = [];
  const wake = createWakeSignal({ playChime: (k) => chimes.push(k) });
  wake.handle({ state: 'active' });
  wake.reset();
  wake.handle({ state: 'active' }); // after reset, same state applies again
  eq(chimes, ['active', 'active'], 'reset clears the de-dup memory');
}

// --- works without motion/agentState (3D page passes a no-op motion) --------
{
  const chimes = [];
  const wake = createWakeSignal({ playChime: (k) => chimes.push(k) });
  wake.handle({ state: 'active' });
  wake.handle({ state: 'dormant' });
  eq(chimes, ['active', 'dormant'], 'chime still plays with no motion/badge wired');
}

// --- agent-state: dormant pins «спит», survives lk.agent.state updates ------
{
  const badge = fakeBadge();
  const as = createAgentState(badge);

  as.set('listening');
  eq(badge.textContent, 'агент: слушает', 'normal state renders');

  as.setDormant();
  eq(badge.textContent, 'агент: спит', 'dormant shows «спит»');
  eq(badge.className, 'badge idle', 'dormant badge is idle-styled');

  // While Dormant the agent stops publishing lk.agent.state; a stray update
  // must NOT overwrite «спит».
  as.set('speaking');
  eq(badge.textContent, 'агент: спит', 'a state update while dormant does not override «спит»');

  as.setAwake();
  eq(badge.textContent, 'агент: слушает', 'waking renders the just-woken listening state');

  // After waking, normal updates flow again.
  as.set('thinking');
  eq(badge.textContent, 'агент: думает', 'state updates resume after waking');
}

console.log(`wake.test.mjs: ${assertions} assertions passed`);
