// Agent-state badge: maps the LiveKit `lk.agent.state` attribute to a
// localized label/colour and keeps it in sync with a participant.
export function createAgentState(badgeEl) {
  function set(state) {
    const map = { listening: ['слушает','ok'], thinking: ['думает','warn'], speaking: ['говорит','ok'], initializing: ['инициализация','idle'] };
    const [label, cls] = map[state] || [state || '—', 'idle'];
    badgeEl.textContent = `агент: ${label}`;
    badgeEl.className = `badge ${cls}`;
  }

  function watch(participant) {
    const apply = () => {
      const st = participant.attributes && participant.attributes['lk.agent.state'];
      if (st) set(st);
    };
    apply();
    participant.on('attributesChanged', apply);
  }

  return { set, watch };
}
