// Entry/wiring module: the ONLY module that touches the DOM by id. Resolves
// every element the modules need, instantiates the controllers, and connects
// the Connect/Mute buttons to the room controller's hook contract.
import { createLog } from './log.js';
import { createAgentState } from './agent-state.js';
import { createVuMeter } from './vu.js';
import { createTranscript } from './transcript.js';
import { createOps } from './ops.js';
import { createRoomController } from './room.js';
import { createAvatar } from './avatar.js';
import { createMotionController } from './motion.js';
import { createLipSync } from './lipsync.js';
import { createMouth } from './mouth.js';
import { createFacial } from './facial.js';
import { createBlendshapes } from './blendshapes.js';

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
const avatar = createAvatar(avatarEl, { log: logger.log });
const motion = createMotionController(avatar);
// Mouth-opening controller: the single caller of avatar.setMouthOpen(). A2F
// blendshapes drive the mouth by default; `?lipsync=volume` forces the old volume
// analyser (ADR-0013 toggle / retreat). The volume analyser (lipsync.js) is reused
// verbatim by feeding it mouth.volumeSink instead of the avatar directly.
const forceVolume = new URLSearchParams(location.search).get('lipsync') === 'volume';
const mouth = createMouth(avatar, { log: logger.log, forceVolume });
const lipsync = createLipSync(mouth.volumeSink, { log: logger.log });
// A2F loose-sync face (eyes/gaze/brows/squint/mouth-form) + the DataChannel
// consumer that drives both face and mouth from `voiceagent` blendshape frames.
const facial = createFacial(avatar);
const blendshapes = createBlendshapes({ facial, mouth, log: logger.log });

let lastAgentState = null;

avatar.init().then((ok) => {
  if (ok) {
    const h = document.getElementById('avatarHint');
    if (h) h.remove();
    motion.setState(lastAgentState);
  }
}).catch((e) => logger.log('аватар: ' + e.message));

window.addEventListener('beforeunload', () => { try { avatar.dispose(); } catch (e) {} });

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
  onAgentState: (state) => { lastAgentState = state; agentState.set(state); motion.setState(state); },
  watchAgentParticipant: agentState.watch,
  attachAudio: (el) => document.body.appendChild(el),
  onMicActive() { micLabelEl.textContent = 'активен'; },
  onMicInactive() { micLabelEl.textContent = 'не активен'; vuBarEl.style.width = '0%'; },
  setMuteEnabled(enabled) { muteBtn.disabled = !enabled; },
  resetMuteUI() { muteBtn.textContent = '🔇 Mute'; micLabelEl.textContent = 'не активен'; },
  transcript,
  ops,
  vu,
  lipsync,
  blendshapes,
};

function doConnect() {
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
