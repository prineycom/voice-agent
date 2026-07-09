// Entry/wiring module for the 3D-face page. Additive sibling of main.js: reuses
// the LiveKit audio, transcript, ops, agent-state, and the A2F DataChannel
// consumer (blendshapes.js) UNCHANGED — the only new part is the 3D renderer
// (face3d-renderer.js) and its sinks (face3d-sinks.js). No Live2D, no volume
// lip-sync, no motion controller (M1 = static head).
import { createLog } from './log.js';
import { createAgentState } from './agent-state.js';
import { createVuMeter } from './vu.js';
import { createTranscript } from './transcript.js';
import { createOps } from './ops.js';
import { createRoomController } from './room.js';
import { createBlendshapes } from './blendshapes.js';
import { createFaceRenderer } from './face3d-renderer.js';
import { createFaceSinks } from './face3d-sinks.js';

const connectBtn = document.getElementById('connectBtn');
const muteBtn = document.getElementById('muteBtn');
const connStateEl = document.getElementById('connState');
const agentStateEl = document.getElementById('agentState');
const latencyEl = document.getElementById('latency');
const vuBarEl = document.getElementById('vuBar');
const micLabelEl = document.getElementById('micLabel');
const transcriptEl = document.getElementById('transcript');
const logEl = document.getElementById('log');
const opsEl = document.getElementById('ops');
const toolfeedEl = document.getElementById('toolfeed');
const sidebarBtn = document.getElementById('sidebarBtn');
const asideEl = document.querySelector('aside');
const scrimEl = document.getElementById('scrim');
const composerEl = document.getElementById('composer');
const composerInputEl = document.getElementById('composerInput');
const composerSendEl = document.getElementById('composerSend');

const logger = createLog(logEl);
const agentState = createAgentState(agentStateEl);
const vu = createVuMeter(vuBarEl, { log: logger.log });

const avatarEl = document.getElementById('avatar');
// 3D renderer + its A2F sinks. The blendshapes consumer drives facial/mouth
// exactly as it drives the Live2D sinks on the other page — the sinks push
// ARKit morphs into the three.js rig instead of the Cubism model.
const renderer = createFaceRenderer(avatarEl, { log: logger.log });
const { facial, mouth } = createFaceSinks(renderer, { log: logger.log });
const blendshapes = createBlendshapes({ facial, mouth, log: logger.log });

// M1 = static head: no state-driven head motion yet. No-op stub so the hook
// shape matches main.js (setState/applyMotionEvent are called but do nothing).
const motion = { setState() {}, applyMotionEvent() {} };

let lastAgentState = null;

// Offline demo replayer: ?demo=joy|anger replays a recorded A2F calibration
// capture through window.__a2fInject after the face is loaded (issue #46 AC#3).
const demo = new URLSearchParams(location.search).get('demo');

// Handle to a running offline demo. A live connection reclaims __a2fInject by
// calling demoHandle.stop() in doConnect() so replay + stream don't collide.
let demoHandle = null;

renderer.init().then((ok) => {
  const h = document.getElementById('avatarHint');
  if (ok) {
    if (h) h.remove();
  } else if (h) {
    h.textContent = 'не удалось загрузить 3D-аватар';
  }
  if (ok && (demo === 'joy' || demo === 'anger')) {
    import('./face3d-demo.js').then((m) => { demoHandle = m.startFaceDemo(demo, { log: logger.log }); });
  }
});

window.addEventListener('beforeunload', () => { try { renderer.dispose(); } catch (e) {} });

function onConn(text, cls) {
  connStateEl.textContent = text;
  connStateEl.className = `badge ${cls}`;
}

function onError() {
  connectBtn.disabled = false;
}

const room = createRoomController({ log: logger.log, onConn, onError });

const transcript = createTranscript(transcriptEl, {
  isLocal: room.isLocal,
  onLatency: (ms) => { latencyEl.textContent = ms; },
  log: logger.log,
});
const ops = createOps(opsEl, toolfeedEl, { log: logger.log, onMotion: (evt) => motion.applyMotionEvent(evt) });

// Hook set room.js destructures from connect(opts) and reuses on disconnect.
// No `lipsync` key: room.js guards every lipsync call with `if (opts.lipsync)`,
// so omitting it disables the volume analyser without a room.js edit.
const hooks = {
  onConnecting() { connectBtn.disabled = true; },
  onConnected() {
    connectBtn.textContent = 'Disconnect';
    connectBtn.className = 'danger';
    connectBtn.disabled = false;
    connectBtn.onclick = () => room.disconnect();
    setComposerEnabled(true);
  },
  onDisconnectedUI() {
    connectBtn.textContent = 'Connect';
    connectBtn.className = 'primary';
    connectBtn.disabled = false;
    connectBtn.onclick = doConnect;
    setComposerEnabled(false);
  },
  onAgentState: (state) => {
    // Leaving 'speaking' = agent audio stopped (playout done or barge-in):
    // bound the A2F face tail instead of playing out a stale buffer.
    if (lastAgentState === 'speaking' && state !== 'speaking') blendshapes.audioStopped();
    lastAgentState = state;
    agentState.set(state);
    motion.setState(state);
  },
  watchAgentParticipant: agentState.watch,
  attachAudio: (el) => document.body.appendChild(el),
  onMicActive() { micLabelEl.textContent = 'активен'; },
  onMicInactive() { micLabelEl.textContent = 'не активен'; vuBarEl.style.width = '0%'; },
  setMuteEnabled(enabled) { muteBtn.disabled = !enabled; },
  resetMuteUI() { muteBtn.textContent = '🔇 Mute'; micLabelEl.textContent = 'не активен'; },
  transcript,
  ops,
  vu,
  blendshapes,
};

function doConnect() {
  // A live stream owns the face: stop any running offline demo so both don't
  // feed __a2fInject at once.
  if (demoHandle) demoHandle.stop();
  room.connect(hooks);
}

connectBtn.onclick = doConnect;

// Text composer: type a message and the agent reacts to it exactly like a
// spoken turn (LiveKit 'lk.chat' text path → LLM → TTS). Enabled only while
// connected. The user's line is echoed locally since the agent does not send
// typed text back as a transcription.
function setComposerEnabled(enabled) {
  composerInputEl.disabled = !enabled;
  composerSendEl.disabled = !enabled;
  if (!enabled) composerInputEl.value = '';
}

composerEl.addEventListener('submit', (e) => {
  e.preventDefault();
  const text = composerInputEl.value.trim();
  if (!text || composerInputEl.disabled) return;
  transcript.addLocalMessage(text);
  room.sendText(text);
  composerInputEl.value = '';
  composerInputEl.focus();
});

function setSidebar(open) {
  asideEl.classList.toggle('open', open);
  scrimEl.classList.toggle('open', open);
  sidebarBtn.setAttribute('aria-expanded', String(open));
}
sidebarBtn.onclick = () => setSidebar(!asideEl.classList.contains('open'));
scrimEl.onclick = () => setSidebar(false);

muteBtn.onclick = async () => {
  const muted = await room.toggleMute();
  muteBtn.textContent = muted ? '🎤 Unmute' : '🔇 Mute';
  micLabelEl.textContent = muted ? 'выключен' : 'активен';
};

logger.log('готов. SDK ' + (window.LivekitClient.version || '2.x'));
