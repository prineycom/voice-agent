// Pure nonlinear expander for the ARKit→Live2D face map (arkit-map.js imports
// this; this module must NOT import back — keep it dependency-free and acyclic).
// Raw ARKit blendshapes read timid on an anime avatar: a noise floor jitters the
// rest frame and the expressive mid-range never reaches the model's dynamic. So
// per group we compress the floor toward 0 and lift the mid-range while pinning
// the endpoints, keeping the shaping as tunable *data* (GROUP_CURVES) rather than
// code. Pure: no DOM, no globals, one number in / one number out per call.

const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);

// Nonlinear expander. Returns +0 (never -0) for x===0 — downstream relies on the
// rest frame staying +0 (arkit-map.js's `0 - g()` idiom). Odd for signed ranges:
// expand(-x,c) === -expand(x,c). Endpoints hold exactly (0→+0, ±1→±1) and the
// output is monotonic non-decreasing in |x|; between, the noise floor compresses
// and the mid-range amplifies.
export function expand(x, curve) {
  if (x === 0) return 0;
  const { floor, gain, range } = curve;
  const s = x < 0 ? -1 : 1;
  const a = x < 0 ? -x : x;
  // Noise-floor compression: renormalize [floor,1] → [0,1]; below the floor → 0.
  const t = (a - floor) / (1 - floor);
  if (t <= 0) return clamp(0, range[0], range[1]);
  // Mid-range gain: exponent < 1 bulges the curve upward (mid input → larger
  // output) while pinning t=0→0 and t=1→1, so the endpoints never move.
  const shaped = Math.pow(t, 1 / gain);
  return clamp(s * shaped, range[0], range[1]);
}

// Per-group tuning knobs. `range` is [-1,1] for signed groups (a value can swing
// either way) and [0,1] for unsigned ones. `floor` (~0.05) is the noise floor to
// compress; `gain` (~1.4–1.8) is mild-to-moderate mid-range amplification tuned
// for an anime face — expressive, not grotesque. Edit these to retune; no code
// change needed. Keys line up with arkit-map.js's output groups.
export const GROUP_CURVES = {
  // Signed: brow height (ParamBrowLY/RY) + brow angle (ParamBrowLAngle/RAngle).
  brows: { floor: 0.05, gain: 1.6, range: [-1, 1] },
  // Unsigned: squint-driven eye smile (ParamEyeLSmile/RSmile), 0..1.
  eyes: { floor: 0.05, gain: 1.5, range: [0, 1] },
  // Signed: smile − frown − pucker (ParamMouthForm).
  mouthForm: { floor: 0.05, gain: 1.6, range: [-1, 1] },
  // Unsigned: cheek squint (ParamCheek), 0..1.
  cheeks: { floor: 0.05, gain: 1.4, range: [0, 1] },
};
