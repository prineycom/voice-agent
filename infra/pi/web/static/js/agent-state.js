// Agent-state badge: maps the LiveKit `lk.agent.state` attribute to a
// localized label/colour and keeps it in sync with a participant. Also reflects
// the wake-word Dormant/Active state (#60): while Dormant the badge shows «спит»
// and pins there (the agent stops publishing lk.agent.state while asleep, so an
// incoming state update must not overwrite «спит» until it wakes).
export function createAgentState(badgeEl) {
  let dormant = false;
  let last = null; // last lk.agent.state seen, re-applied on wake

  function render(state) {
    const map = { listening: ['слушает','ok'], thinking: ['думает','warn'], speaking: ['говорит','ok'], initializing: ['инициализация','idle'] };
    const [label, cls] = map[state] || [state || '—', 'idle'];
    badgeEl.textContent = `агент: ${label}`;
    badgeEl.className = `badge ${cls}`;
  }

  function set(state) {
    last = state;
    if (dormant) return; // «спит» wins until the agent wakes
    render(state);
  }

  // Wake-word transitions (#60).
  function setDormant() {
    dormant = true;
    badgeEl.textContent = 'агент: спит';
    badgeEl.className = 'badge idle';
  }

  function setAwake() {
    dormant = false;
    render('listening'); // the natural just-woken state; the agent's real state follows
  }

  function watch(participant) {
    const apply = () => {
      const st = participant.attributes && participant.attributes['lk.agent.state'];
      if (st) set(st);
    };
    apply();
    participant.on('attributesChanged', apply);
  }

  return { set, watch, setDormant, setAwake };
}
