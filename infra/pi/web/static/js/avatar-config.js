// Avatar profiles: each ties a Cubism-4 model to the state->motion and
// emotion->expression maps that fit *that model's* rig. Different models ship
// different motion groups and expression sets, so these maps are per-model and
// cannot be shared blindly.
//
// To switch avatars — or revert — change ACTIVE_AVATAR to a key below. The old
// avatar's full config is retained here on purpose so a previous presentation
// can be restored by flipping one line (no asset or code deletion required).

export const AVATAR_PROFILES = {
  // Previous avatar. Kept verbatim so 'natori' remains a one-line revert.
  // Ships expression files (Normal/Smile/Sad/Surprised/Blushing/…). Motion
  // groups: Idle (3 motions) + TapBody (5 motions).
  natori: {
    model: 'static/models/natori/Natori.model3.json',
    motions: {
      idle:      { group: 'Idle',    index: 0, expression: 'Normal' },
      listening: { group: 'Idle',    index: 1, expression: 'Normal' },
      thinking:  { group: 'TapBody', index: 0, expression: 'Blushing' },
      speaking:  { group: 'TapBody', index: 2, expression: 'Smile' },
    },
    emotionExpr: { neutral: 'Normal', happy: 'Smile', sad: 'Sad', surprised: 'Surprised', thinking: 'Blushing' },
    fallbackExpr: 'Normal',
  },

  // Current avatar. Hiyori has NO expression files, so every expression is null
  // (setExpression no-ops on null). Motion groups: Idle (9 motions, 0..8) +
  // TapBody (1 motion, index 0) — note the old 'speaking: TapBody/2' would be
  // out of range here, so speaking maps to TapBody/0 and the extra idle
  // variations are spread across the other states.
  hiyori: {
    model: 'static/models/hiyori/Hiyori.model3.json',
    motions: {
      idle:      { group: 'Idle',    index: 0, expression: null },
      listening: { group: 'Idle',    index: 1, expression: null },
      thinking:  { group: 'Idle',    index: 2, expression: null },
      speaking:  { group: 'TapBody', index: 0, expression: null },
    },
    emotionExpr: { neutral: null, happy: null, sad: null, surprised: null, thinking: null },
    fallbackExpr: null,
  },
};

// The active avatar. Flip to 'natori' to restore the previous avatar.
export const ACTIVE_AVATAR = 'hiyori';

export const activeProfile = AVATAR_PROFILES[ACTIVE_AVATAR];
