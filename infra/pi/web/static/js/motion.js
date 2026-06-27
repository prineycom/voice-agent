// Motion policy: maps an agent state to a Live2D motion + expression on the
// injected avatar. No DOM, no globals — pure state -> motion mapping with
// debounce on identical states.
export function createMotionController(avatar) {
  const table = {
    idle:      { group: 'Idle',    index: 0, expression: 'Normal' },
    listening: { group: 'Idle',    index: 1, expression: 'Normal' },
    thinking:  { group: 'TapBody', index: 0, expression: 'Blushing' },
    speaking:  { group: 'TapBody', index: 2, expression: 'Smile' },
  };

  function resolve(state) {
    const key = String(state).toLowerCase();
    if (key === 'listening' || key === 'thinking' || key === 'speaking') return key;
    return 'idle';
  }

  let current = null;

  function setState(state) {
    const key = resolve(state);
    if (key === current) return;
    current = key;
    const entry = table[key];
    avatar.playMotion(entry.group, entry.index);
    avatar.setExpression(entry.expression);
  }

  return { setState };
}
