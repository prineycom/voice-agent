// Discrete "vtuber accent" state machine layered ON TOP of the continuous
// ARKit→Live2D face map (arkit-map.js). Where the base map is a smooth,
// per-frame mirror of the A2F ARKit signal, this module watches a few of those
// signals and fires threshold-triggered stylised poses — a joy squint, an
// amazement wide-eye, a sustained-joy head tilt — that read as deliberate
// expression beats rather than raw muscle mirroring.
//
// Anti-flicker comes from three stacked guards per accent:
//   1. Schmitt dual threshold (on > off) — the signal must cross `on` to latch
//      and fall below `off` to release, so noise around a single edge can't
//      chatter the latch.
//   2. Sustain gate — the signal must stay >= on for `sustainMs` before the
//      latch actually turns on, rejecting brief spikes.
//   3. One-pole easing envelope — a 0..1 value eased toward the latch state so
//      the pose pops in / releases smoothly instead of snapping.
//
// PURE: no DOM, no globals, deterministic given prior state + input + the
// injected clock. `now` defaults to performance.now (present in browser and
// Node globalThis) but nothing here touches document/window.
//
// Contract with the last-writer sink: an accent that contributes nothing OMITS
// its keys entirely (never writes 0 / neutral), so the sink falls back to the
// base map / motion / physics. It never emits ParamMouthOpenY or JawOpen —
// mouth-*opening* is owned by the lip-sync provider (mouth.js).

const EPS = 1e-3;        // envelope below this ≈ off; keys omitted
const EPS_ANGLE = 0.1;   // ease-to-zero release: omit ParamAngleZ within this of 0
const ALPHA = 0.3;       // base one-pole easing rate — same lerp idiom as mouth.js (0.5), deliberately tuned slower (0.3) for accent pacing
const ALPHA_RELEASE = 0.12; // slower release for ease-to-zero accents (head tilt)

// Accent descriptors — all tunable data. `signal(g)` reads the sparse ARKit
// frame via g (missing key = 0). `on`/`off` are the Schmitt thresholds,
// `sustainMs` the pre-latch dwell, `target` the params written while active,
// `release` either 'instant' (base-owned param, drop on release) or
// 'ease-to-zero' (non-base-owned param, decay smoothly before dropping).
const ACCENTS = [
  {
    // Joy squint: a broad smile crinkles the eyes. Only the eye-smile targets
    // are the accent's contribution — ParamMouthForm is deliberately NOT a
    // target because the base map already drives it near-full on a sustained
    // strong smile, and re-asserting it here would dip-then-ramp (un-smile then
    // re-smile) as the envelope eases up. Both targets are base-owned, so an
    // instant release just hands control back to the continuous map.
    id: 'joy-squint',
    signal: g => (g('MouthSmileLeft') + g('MouthSmileRight')) / 2,
    on: 0.6,
    off: 0.4,
    sustainMs: 250,
    target: { ParamEyeLSmile: 1, ParamEyeRSmile: 1 },
    release: 'instant',
  },
  {
    // Amazement wide-eyes: eyes fly open PAST the base clamp of 1 and brows
    // lift. The two ParamEye*Open interpolate from 1 (not 0) so a partial
    // envelope widens rather than dips the lid closed.
    id: 'amazement-wide-eyes',
    signal: g => Math.max(g('EyeWideLeft'), g('EyeWideRight')),
    on: 0.5,
    off: 0.3,
    sustainMs: 120,
    target: { ParamEyeLOpen: 1.6, ParamEyeROpen: 1.6, ParamBrowLY: 1, ParamBrowRY: 1 },
    release: 'instant',
  },
  {
    // Head tilt on SUSTAINED joy — a distinct, longer dwell than joy-squint.
    // ParamAngleZ is owned by motions + physics, not the base map, so on
    // release we decay it smoothly to zero before dropping the key to bound
    // the snap when control hands back.
    id: 'head-tilt',
    signal: g => (g('MouthSmileLeft') + g('MouthSmileRight')) / 2,
    on: 0.55,
    off: 0.35,
    sustainMs: 600,
    target: { ParamAngleZ: 9 },
    release: 'ease-to-zero',
  },
];

export function createAccents({ now } = {}) {
  now = now || (() => performance.now());

  // Per-accent runtime state, index-aligned with ACCENTS.
  const state = ACCENTS.map(() => ({ active: false, sinceAbove: null, env: 0 }));

  function reset() {
    for (const s of state) {
      s.active = false;
      s.sinceAbove = null;
      s.env = 0;
    }
  }

  function step(arkit) {
    const g = k => arkit[k] || 0;
    const t = now();
    const out = {};

    for (let i = 0; i < ACCENTS.length; i++) {
      const a = ACCENTS[i];
      const s = state[i];
      const sig = a.signal(g);

      // Schmitt + sustain latch.
      if (s.active) {
        if (sig < a.off) { s.active = false; s.sinceAbove = null; }
      } else {
        if (sig >= a.on) {
          if (s.sinceAbove === null) s.sinceAbove = t;
          if (t - s.sinceAbove >= a.sustainMs) s.active = true;
        } else {
          s.sinceAbove = null;
        }
      }

      // One-pole easing envelope toward the latch state. Head-tilt style
      // accents ease DOWN slowly (ease-to-zero release) so the pose settles.
      const goal = s.active ? 1 : 0;
      const alpha = (!s.active && a.release === 'ease-to-zero') ? ALPHA_RELEASE : ALPHA;
      s.env += (goal - s.env) * alpha;

      const env = s.env;

      if (a.release === 'ease-to-zero') {
        // Non-base-owned param: keep writing a decaying value until it is within
        // EPS_ANGLE of zero, then omit the key so motion/physics reclaim it.
        for (const paramId in a.target) {
          const value = a.target[paramId] * env;
          if (s.active || Math.abs(value) > EPS_ANGLE) out[paramId] = value;
        }
        continue;
      }

      // Base-owned param: contribute only while active or the envelope is still
      // audible; otherwise omit so the base map shows through.
      if (!s.active && env <= EPS) continue;

      for (const paramId in a.target) {
        const tv = a.target[paramId];
        // Eye-open targets sit above the base value of 1, so interpolate from 1
        // toward the target — a partial envelope widens rather than closes.
        out[paramId] = (paramId === 'ParamEyeLOpen' || paramId === 'ParamEyeROpen')
          ? 1 + (tv - 1) * env
          : tv * env;
      }
    }

    return out;
  }

  return { step, reset };
}
