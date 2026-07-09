# Live2D amplification: expander curves + discrete vtuber accents — implementation plan

**Task:** GitHub issue #41 (https://github.com/prineycom/voice-agent/issues/41)
**Complexity:** medium
**Mode:** sub-agents
**Parallel:** true

Frontend only (vanilla ES modules, no build step). Make the A2F-driven face read as
expressive on the Live2D anime avatar: replace the linear 52→12 pass-through mapper
with (a) nonlinear amplification (expander curves) and (b) discrete vtuber-style accents.

## Key architectural finding that shapes the design

`avatar.setFaceParams(map)` is an **absolute last-writer sink, per key, per frame**
(`avatar.js:83-88`): keys present in the map override motion + physics; **omitted keys
fall back** to motion / physics / auto-blink. Two consequences drive the design:

1. **Wide-eyes needs no edit to `arkit-map.js`'s `clamp01`.** Accents run *after* the
   base map via `Object.assign`, so an accent writing `ParamEyeLOpen: 1.6` overwrites the
   base's clamped `1`. We bypass the clamp by *layering*, not by un-clamping the pure
   mapper — existing `arkit-map.test.mjs` assertions stay untouched.
2. **Ship only accents whose params already exist in the base map** (ParamEyeL/ROpen,
   ParamEyeL/RSmile, ParamBrowL/RY, ParamMouthForm). Their ease-out-then-omit falls back
   to a *written* base value → seamless release. Head angles (ParamAngleX/Z) are **not**
   in the base map, so omitting them snaps to whatever motion/physics is mid-keyframe →
   the documented stutter risk. This is the decisive reason to **defer head-tilt** (DD-4).

## Design decisions

### DD-1: Expander lives in a new pure `expander.js`, imported by `arkit-map.js`

**Decision:** A stateless module exports `expand(x, curve)` plus a `GROUP_CURVES` config
table; `arkit-map.js` imports it and wraps each expression param in its group's curve.
**Rationale:** Mirrors the existing split where `mouth.js:34 a2fMouthOpen` is a
separately-exported pure shaping fn testable independently of its stateful controller.
Keeps `arkit-map.js` a thin group→param assembler and makes the curve config a **data-table
edit, not a code change** (matches the repo's env-knob tuning philosophy). Tests can
`import { expand }` directly, like they import `arkitToLive2D`.
**Alternative:** Inline the curve + config inside `arkit-map.js` — rejected: bloats the
mapper and couples every tuning edit to a mapper edit.

### DD-2: Curve has fixed endpoints at 0 and ±1, odd for signed groups

**Decision:** `expand` maps `0→+0` (not `-0`), `±1→±1`, is monotonic, compresses a noise
floor near 0 (small→smaller) and applies gain>1 in the mid-range (medium→large), then
clamps to the group's range. For signed groups (brows, mouth-form) it is odd:
`expand(-x) = -expand(x)`.
**Rationale:** `arkit-map.test.mjs` asserts only fixed points — neutral→0 (every non-eye
key), smile→+1, frown→-1, browDown→-1. Fixing endpoints at 0/±1 keeps **all existing
assertions green** while amplifying the untested mid-range. Preserving `+0` protects the
`0 - g()` idiom (`arkit-map.js:30`) the neutral test relies on.
**Alternative:** A curve that rescales endpoints — rejected: flips existing extreme-value
assertions to red and forces a baseline-test rewrite.

### DD-3: Expander groups exclude eye-lid open and gaze

**Decision:** Expander applies only to expression-from-zero params: `eyes` =
ParamEyeL/RSmile (squint), `brows` = ParamBrowL/RY + ParamBrowL/RAngle, `mouthForm` =
ParamMouthForm, `cheeks` = ParamCheek. It does **not** touch ParamEyeL/ROpen (that is
`1 - blink`, a lid baseline) or ParamEyeBallX/Y (gaze).
**Rationale:** Expanding `1 - blink` would distort blink dynamics and break the
blink/neutral eye-open=1 assertions; the issue's "(brows, eyes, mouth-form, cheeks)" means
squint, not lid.
**Alternative:** Expand every output param — rejected: breaks blink and gaze.

### DD-4: Accents live in a new pure stateful module `accents.js`; head-tilt is deferred

**Decision:** `createAccents({ now } = {})` returns `{ step(arkit) → {params}, reset() }`.
It holds per-accent latch + sustain-timer + easing-envelope state (no DOM/globals;
deterministic given prior state + input + injected clock). `facial.js` layers it over the
base map; `release()` calls `reset()`. Ship exactly the two required accents (joy-squint,
amazement-wide-eyes); **defer** the optional head-tilt.
**Rationale:** Keeps stateful hysteresis in a **separately unit-testable** module (the
constraint: not buried in DOM-coupled `facial.js`). Head-tilt is deferred because its
target params (ParamAngleX/Z) are not base-owned, so the omit-on-off contract cannot fall
back cleanly → physics/motion snap (documented risk). AC requires only two accents; both
shipped accents touch base-owned params exclusively, so "releases cleanly / no stutter" is
satisfied by construction.
**Alternative:** Ship head-tilt now behind easing — rejected: still risks the snap on
mid-motion release and adds an untestable coupling to motion/physics timing.

### DD-5: Anti-flicker = Schmitt dual-threshold + sustain gate + easing envelope

**Decision:** Each accent config carries `{ signal(arkit), onThreshold, offThreshold,
sustainMs, params }` with `onThreshold > offThreshold`. The latch turns on only after
`signal ≥ onThreshold` held continuously for `sustainMs`; turns off when
`signal < offThreshold`. A one-pole envelope (alpha ~0.3, mirroring `mouth.js:140
cur += (open-cur)*0.5`) eases each accent's contribution in/out. **Accent OFF = omit the
key**: when the latch is off *and* the envelope ≈ 0, `step` does not add the accent's keys
at all (fall back to base). Timing uses the injected `now`.
**Rationale:** Dual-threshold defeats frame-to-frame chatter around a single threshold; the
sustain gate implements "sustained high joy"; the envelope prevents pose pops. Because
every accent param exists in the base map, easing out then omitting falls back to a written
base value → no snap.
**Alternative:** Single threshold with per-frame trigger — rejected: flickers, fails the
explicit no-flicker AC.

### DD-6: Wiring is minimal and signature-stable

**Decision:** `createFacial(avatar, { accents = null } = {})`; `apply(arkit)` =
`const p = arkitToLive2D(arkit); if (accents) Object.assign(p, accents.step(arkit));
avatar.setFaceParams(p);`; `release()` = `if (accents) accents.reset();
avatar.setFaceParams(null);`. `main.js` constructs
`createFacial(avatar, { accents: createAccents() })`. `blendshapes.js` `apply(f)` /
`onReplyEnd` unchanged (already calls `facial.apply(f.arkit)` then `facial.release()`).
**Rationale:** `facial.apply`'s external signature stays `(arkit)`, so `blendshapes.js`,
the scheduler, the provider split, and `window.__a2fInject` are untouched. `?facedebug=1`
reads raw arkit via its own inline math (`facedebug.js:50-68`) and is unaffected by
definition.
**Alternative:** Push accents into `blendshapes.js` — rejected: spreads face logic across
files and touches the scheduler path the issue marks off-limits.

### DD-7: The two concrete accents

| Accent | Trigger signal (from arkit) | on / off / sustain | Params while active (all base-owned) |
|---|---|---|---|
| **joy-squint** | `(MouthSmileLeft+MouthSmileRight)/2` (optionally +CheekSquint) | 0.6 / 0.4 / ~250 ms | `ParamEyeLSmile:1, ParamEyeRSmile:1, ParamMouthForm` boosted (~1.0) |
| **amazement-wide-eyes** | `max(EyeWideLeft, EyeWideRight)` (optionally +BrowInnerUp) | 0.5 / 0.3 / ~120 ms | `ParamEyeLOpen:1.6, ParamEyeROpen:1.6` (past 1.0 via layering), `ParamBrowLY:1, ParamBrowRY:1` (brow pop) |

Thresholds/targets are config values in `accents.js`, tunable as data. Neither accent emits
ParamMouthOpenY or JawOpen (mouth *open* stays owned by `mouth.js`; mouth *form* is fair game).

### DD-8: A/B verdict is recorded in the execution report + issue comment (no new ADR)

**Decision:** The A/B comparison (emotional reply via A2F vs `?lipsync=volume`, both with
`?facedebug=1` for a raw-frame reference) and its written verdict are recorded in the
execution report and posted as a comment on issue #41. No new ADR.
**Rationale:** The AC wording is "a written verdict in the issue"; existing ADR numbers
0014/0016 are already taken, so a new ADR would collide and adds no durable decision here.
**Alternative:** A new ADR — rejected: numbering collision, and this is an observation, not
an architectural decision.

### DD-9: Automated A/B evidence + user perceived-effect acceptance

**Decision:** T7 produces the mechanical A/B evidence — driven repeatably through
`window.__a2fInject` in a served headless browser (the #40/#44-45 xvfb recipe): captured
Live2D param amplitudes A2F-with-amplification vs the same frames without, plus a
neutral-speech "not grotesque" check. The **final perceived-effect acceptance** (the
avatar is *obviously* more alive on an emotional reply) is a user visual check from the
live frontend, gated after the run — the same acceptance model as #44/#45.
**Rationale:** The acceptance bar is explicitly a perceived-effect comparison, which a unit
test cannot assert; the agent supplies measurable evidence, the human supplies the verdict.
**Alternative:** Claim acceptance from automated captures alone — rejected: contradicts the
issue's stated acceptance bar.

## Tasks

### Task 1: `expander.js` — pure curve + per-group config

- **Files:** `infra/pi/web/static/js/expander.js` (create)
- **Depends on:** none
- **Scope:** S–M
- **What:** Export `expand(x, curve)` and a `GROUP_CURVES` table for groups
  `brows, eyes, mouthForm, cheeks`.
- **How:** `expand`: return `+0` when `x === 0`; take sign + abs, subtract/renormalize a
  `floor` (noise compression), apply gain>1 mid expansion, clamp to `curve.range`
  (`[-1,1]` or `[0,1]`); re-apply sign (odd for signed ranges). Reuse the `clamp/clamp01`
  idiom (`arkit-map.js:9-10`). Endpoints must satisfy `expand(0)=+0`, `expand(±1)=±1`,
  monotonic between.
- **Context:** `infra/pi/web/static/js/mouth.js:34` (a2fMouthOpen pure-helper template);
  `infra/pi/web/static/js/arkit-map.js:9-10` (clamp helpers).
- **Verify:** `node -e` import clean; `Object.is(expand(0,c), 0)` true, `expand(1,c)===1`,
  `expand(0.5,c)>0.5`, `expand(0.03,c)<0.03`.

### Task 2: Integrate expander into `arkit-map.js`

- **Files:** `infra/pi/web/static/js/arkit-map.js` (edit)
- **Depends on:** Task 1
- **Scope:** S
- **What:** Wrap the expression params in their group curve; leave ParamEyeL/ROpen and
  ParamEyeBallX/Y unchanged.
- **How:** `import { expand, GROUP_CURVES }`; apply `expand(value, GROUP_CURVES.<group>)`
  to ParamEyeL/RSmile, ParamBrowL/RY, ParamBrowL/RAngle, ParamMouthForm, ParamCheek.
  Preserve the `0 - g()` idiom for brow angle (`expand(+0)` stays `+0`). Keep the function
  pure — one object literal per call.
- **Context:** `infra/pi/web/static/js/arkit-map.js` (whole 25-line map);
  `infra/pi/web/static/js/avatar.js:79-89,146` (sink pins the returned keys).
- **Verify:** existing `arkit-map.test.mjs` stays green (0 and ±1 fixed points hold).

### Task 3: Expander unit tests

- **Files:** `infra/pi/web/static/js/expander.test.mjs` (create),
  `infra/pi/web/static/js/arkit-map.test.mjs` (extend)
- **Depends on:** Task 1, Task 2
- **Scope:** M
- **What:** Cover the pure curve and the amplified mapper mid-range.
- **How:** `expander.test.mjs`: `Object.is(expand(0), +0)`, endpoints ±1, monotonic
  mid-expansion, floor compression (`expand(0.03) < 0.03`), odd symmetry, clamp beyond
  range. Extend `arkit-map.test.mjs`: mid smile 0.3 → `ParamMouthForm > 0.3`; tiny 0.03 →
  compressed; neutral still all-0; extremes unchanged. Reuse the file's `eq/ok/approx`
  counter idiom, `import assert from 'node:assert/strict'`, end
  `console.log(...passed); process.exit(0)`.
- **Context:** `infra/pi/web/static/js/arkit-map.test.mjs` (test idiom).
- **Verify:** `node --test infra/pi/web/static/js/*.test.mjs 2>&1 | tail -20` — green.

### Task 4: `accents.js` — pure stateful state machine

- **Files:** `infra/pi/web/static/js/accents.js` (create)
- **Depends on:** none
- **Scope:** M
- **What:** `createAccents({ now } = {})` → `{ step(arkit), reset() }` implementing
  joy-squint + amazement-wide-eyes per DD-5/DD-7.
- **How:** Config array of accent descriptors
  `{ id, signal(arkit), on, off, sustainMs, target:{paramId:value} }`. Per-accent state
  `{ active, sinceAbove, env }`. `step`: for each accent compute signal, apply Schmitt
  latch + sustain gate (via `now()`), lerp `env` toward 1/0 (one-pole), and add its keys
  only when `active || env > ε`, scaling target contribution by `env` for a smooth pop-in.
  `reset`: clear all latches/timers/envelopes. Never emit ParamMouthOpenY/JawOpen. Inject
  `now` default `() => performance.now()`.
- **Context:** `infra/pi/web/static/js/mouth.js:140` (one-pole lerp), `mouth.js:21`
  (makeRing, if a time-windowed sustain buffer is preferred); ARKit key names
  `infra/desktop/a2f/arkit.py:13-40`.
- **Verify:** `node -e` importable; a below-threshold `step(arkit)` returns `{}`.

### Task 5: Wire accents into `facial.js` + `main.js`

- **Files:** `infra/pi/web/static/js/facial.js` (edit), `infra/pi/web/static/js/main.js`
  (edit)
- **Depends on:** Task 4
- **Scope:** S
- **What:** Layer accents over the base map; reset on release; construct accents at wiring.
- **How:** `createFacial(avatar, { accents = null } = {})`; `apply` = base map then
  `Object.assign(params, accents.step(arkit))`; `release` calls `accents.reset()` then
  `setFaceParams(null)`. In `main.js`: `import { createAccents }` and
  `createFacial(avatar, { accents: createAccents() })`.
- **Context:** `infra/pi/web/static/js/facial.js` (whole file); `main.js` facial wiring +
  facedebug path (unaffected); `blendshapes.js:43` (call site — must stay unchanged).
- **Verify:** page loads; `blendshapes.js` unchanged; `facial.apply(arkit)` signature
  preserved.

### Task 6: Accent / hysteresis unit tests

- **Files:** `infra/pi/web/static/js/accents.test.mjs` (create)
- **Depends on:** Task 4
- **Scope:** M
- **What:** Threshold, sustain, hysteresis/flicker, release, isolation, and the
  no-mouth-open invariant.
- **How:** `createAccents({ now: () => clock })` with a mutable fake clock. Assert: below
  on-threshold → `{}`; above on but before `sustainMs` → still `{}`; sustained → params
  present and `ParamEyeLOpen > 1`; alternating just-below/just-above off-threshold → stays
  active (Schmitt, no flicker); drop below off → deactivates and key omitted; `reset()`
  clears state; both accents active independently; **no `ParamMouthOpenY`/`JawOpen` ever in
  output**. Reuse the arkit-map.test.mjs counter idiom + `process.exit(0)`.
- **Context:** `infra/pi/web/static/js/arkit-map.test.mjs` (test idiom).
- **Verify:** `node --test infra/pi/web/static/js/*.test.mjs 2>&1 | tail -20` — green.

### Task 7: Validation

- **Files:** — (runs the suite) + written A/B verdict in the execution report + issue comment
- **Depends on:** all
- **Scope:** S–M
- **What:** Full green suite + mechanical A/B evidence + manual guardrails; defer the
  perceived-effect acceptance to a user visual check.
- **How:** Run `node --test infra/pi/web/static/js/*.test.mjs 2>&1 | tail -20` (expect 8
  files green: baseline 6 + expander + accents). Serve the frontend headless (xvfb recipe
  from #40/#44-45), drive repeatable emotional frames via `window.__a2fInject`, and capture
  Live2D param amplitudes with amplification vs without; confirm `?facedebug=1` overlay
  still shows raw frames and `window.__a2fInject` still applies; confirm the face releases
  to idle between turns and neutral speech is not grotesque (no permanent overacting).
  Record the written A/B verdict (DD-8). Final perceived acceptance = user visual check
  (DD-9).
- **Context:** —
- **Verify:** `node --test infra/pi/web/static/js/*.test.mjs 2>&1 | tail -20` — all green;
  A/B verdict written.

## Execution

- **Mode:** sub-agents
- **Parallel:** true
- **Reasoning:** the expander lane {expander.js, arkit-map.js, arkit-map.test.mjs,
  expander.test.mjs} and the accents lane {accents.js, facial.js, main.js,
  accents.test.mjs} have fully disjoint file sets and no shared symbols, converging only at
  Validation; within each lane steps share files and stay sequential.
- **Order:**
  Group 1 (parallel): Task 1, Task 4
  ─── barrier ───
  Group 2 (parallel): Task 2, Task 5, Task 6
  ─── barrier ───
  Group 3: Task 3
  ─── barrier ───
  Group 4: Task 7 (Validation)

## Verification

From issue #41 acceptance criteria:

- Expander curves implemented in the mapping layer, per-group tunable, covered by unit
  tests (extend `arkit-map.test.mjs` style; `node --test infra/pi/web/static/js/*.test.mjs`
  green).
- At least two discrete accents (joy-squint, amazement-wide-eyes) with thresholds/hysteresis
  so they don't flicker frame-to-frame.
- A/B check recorded (written verdict): emotional reply with A2F vs `?lipsync=volume` — A2F
  variant clearly more expressive; neutral speech does not look grotesque (no permanent
  overacting).
- Face still releases cleanly to idle between turns; head-angle accents don't stutter idle
  motions (head-tilt deferred, so no risk this iteration).
- `?facedebug=1` overlay and `window.__a2fInject` still work.

## Materials

- `docs/retro-epic8-a2f.md` — decisions #6, #8, roadmap step 3; the acceptance-bar lesson.
- `docs/adr/0013-a2f-driven-lipsync-volume-fallback.md` — provider split that must survive;
  `docs/adr/0012-audio2face-hybrid-facial-animation.md` — hybrid design.
- Code: `infra/pi/web/static/js/arkit-map.js` (linear mapper to amplify), `facial.js`
  (apply/release), `avatar.js` (`beforeModelUpdate` last-writer sink), `mouth.js`
  (mouth-open owned by lip-sync — do not fight), `blendshapes.js`/`schedule.js` (playback
  path, untouched), `facedebug.js` (raw overlay, decoupled), `arkit-map.test.mjs` (test
  style).
- ARKit-52 key names: `infra/desktop/a2f/arkit.py:13-40`.
- Glossary: `.yoke/context.md` — Blendshape, Facial animation, Expression.
