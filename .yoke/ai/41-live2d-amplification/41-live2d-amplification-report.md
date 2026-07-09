# Report: 41-live2d-amplification

**Plan:** `.yoke/ai/41-live2d-amplification/41-live2d-amplification-plan.md`
**Mode:** sub-agents (parallel, 2 lanes)
**Status:** ✅ complete

Issue #41 — replace the linear 52→12 ARKit→Live2D mapper with (a) nonlinear expander
curves and (b) discrete vtuber-style accents (joy-squint, amazement-wide-eyes, head-tilt),
so the A2F-driven face reads as expressive on the anime avatar. Frontend only, vanilla ES
modules, no build step.

## Tasks

| #  | Task                                                | Status  | Commit    | Concerns |
| -- | --------------------------------------------------- | ------- | --------- | -------- |
| 1  | `expander.js` — pure curve + per-group config       | ✅ DONE | `8308dc6` | —        |
| 2  | Integrate expander into `arkit-map.js`              | ✅ DONE | `161036b` | —        |
| 3  | Expander unit tests (+ extend arkit-map tests)      | ✅ DONE | `871aea3` | —        |
| 4  | `accents.js` — 3-accent state machine               | ✅ DONE | `43c0857` | —        |
| 5  | Wire accents into `facial.js` + `main.js`           | ✅ DONE | `0f0dc80` | —        |
| 6  | Accent / hysteresis unit tests                      | ✅ DONE | `53fb2eb` | 1 minor  |
| 7  | Validation + A/B evidence                           | ✅ DONE | —         | —        |

All six implementation tasks passed the review loop (spec + quality) on the first
iteration. T7 is validation-only (no code commit).

## Post-implementation

| Step          | Status     | Commit    |
| ------------- | ---------- | --------- |
| Validate      | ✅ pass    | —         |
| Documentation | ⏭️ skipped | —         |
| Format        | ⏭️ N/A     | —         |

Documentation was not requested (`--update-docs` off, `update_docs` unset). No linter or
formatter is configured in the project (confirmed prior in #40) — nothing to run.

## Design highlights

- **Layering over the last-writer sink.** `avatar.setFaceParams` pins present keys and lets
  omitted keys fall back to motion/physics/auto-blink. Accents are `Object.assign`-ed over
  the base map (`facial.js`), so "wide eyes past 1.0" is achieved by *overwriting* the base
  clamped `ParamEyeLOpen:1` — no edit to the pure mapper's `clamp01` was needed, and the
  existing `arkit-map.test.mjs` assertions stayed untouched.
- **Expander keeps endpoints fixed** at `0→+0` and `±1→±1`, compresses a noise floor (0.05)
  and lifts the mid-range (gain ~1.4–1.6). Fixing the endpoints is what let the 19 existing
  mapper assertions stay green while amplifying the untested middle. The `+0` (not `-0`)
  rest-frame idiom is preserved through the curve.
- **Anti-flicker accents.** Each accent uses a Schmitt dual-threshold + a `sustainMs` gate
  (via an injectable clock) + a one-pole easing envelope. Accent OFF **omits** the key
  rather than writing 0, so control falls back cleanly.
- **Head-tilt release (DD-10).** `ParamAngleZ` is not base-owned (motions + physics own it),
  so head-tilt uses ease-to-zero-before-omit: on deactivation it keeps writing a
  monotonically decreasing angle until within ε of 0, then omits the key — bounding the
  handback snap to the small idle-sway amplitude around neutral. (Head-tilt was added on
  user request; the plan originally deferred it.)

## Validation

`node --test infra/pi/web/static/js/*.test.mjs` ✅ **8 files, 8 pass, 0 fail — 287 assertions**
(baseline 6 + new `accents.test.mjs` 22 + `expander.test.mjs` 32; `arkit-map.test.mjs`
extended 19→25). No lint / type-check / build steps exist in this project.

### A/B evidence — OLD linear mapper (`cb05498`) vs NEW (expander + accents)

Deterministic 72-frame emotional-reply sweep (~2.40s @30fps, injected clock):
`OLD = oldLinear(arkit)`, `NEW = Object.assign({}, newMap(arkit), accents.step(arkit))`.

| Param            | OLD peak | NEW peak | Factor            |
| ---------------- | -------- | -------- | ----------------- |
| ParamMouthForm   | 0.800    | 1.000    | 1.25×             |
| ParamEyeLSmile   | 0.300    | 1.000    | 3.33×             |
| ParamBrowLY      | 0.000    | 0.998    | new beat (0→1)    |
| ParamEyeLOpen    | 1.000    | 1.599    | 1.60× (past clamp)|
| ParamAngleZ      | 0.000    | 9.000    | new beat (0→9°)   |
| ParamCheek       | 0.600    | 0.677    | 1.13× (expander)  |

Accent first-fire order was correct: joy-squint at 667 ms (after its ~250 ms dwell),
head-tilt only at 933 ms (after the longer ~600 ms sustain), amazement-wide-eyes at 1633 ms
(on the EyeWide spike). `ParamEyeLOpen` exceeded 1.0 only under NEW (1.599 vs OLD's clamped
1.0) — the "eyes fly wide open" read the linear map structurally cannot reach. On release,
`ParamAngleZ` decayed monotonically 9.00 → … → 0.37 and was then omitted within ε (0.1) —
no snap.

### Neutral-speech guard (no overacting)

45-frame low-amplitude jitter sweep (all inputs ≤ 0.1): **no accent latched**, `ParamAngleZ`
never appeared, `ParamEyeLOpen` stayed at 1.0, and expander outputs stayed modest
(~0.12–0.16 peaks — an order of magnitude under the emotional-run peaks). Flat speech does
not overact.

### Integrity (unchanged / decoupled)

- `?facedebug=1` overlay: `facedebug.js` has zero imports and reads raw `evt.arkit` inline —
  decoupled from the mapper/accents, unaffected.
- `window.__a2fInject`: `blendshapes.js` is bit-for-bit unchanged since `cb05498`; injected
  frames still route through the unchanged `facial.apply(f.arkit)` signature.
- Provider split / `?lipsync=volume` / `schedule.js` / `mouth.js`: not in the diff — the
  A2F/volume split and playback scheduler are untouched.
- No accent ever emits `ParamMouthOpenY` / `JawOpen` (asserted over both sweeps) — the
  lip-sync channel is never contended.

## Acceptance vs issue #41

| Criterion | State |
| --- | --- |
| Expander curves in the mapping layer, per-group tunable, unit-tested; suite green | ✅ |
| ≥2 discrete accents with thresholds/hysteresis (no flicker) — here 3 | ✅ |
| A/B check recorded (written verdict); A2F clearly more expressive; neutral not grotesque | ✅ mechanical evidence recorded (below); **live perceived-effect check pending user** |
| Face releases cleanly to idle; head-angle accent doesn't stutter idle motion | ✅ head-tilt ease-to-zero verified |
| `?facedebug=1` overlay and `window.__a2fInject` still work | ✅ decoupled / unchanged |

The one open item is the **final perceived-effect acceptance** — a human visual check on the
live frontend (`https://ai.priney.com` with `?facedebug=1`, an emotional reply compared
against `?lipsync=volume`). The mechanical A/B evidence establishes the amplification and the
guardrails; per DD-9 it sets up that check but does not replace it.

## Concerns

### Task 6: accent hysteresis unit tests

Minor (recorded, non-blocking): `accents.test.mjs` defines an `approx` counter helper (copied
from the `arkit-map.test.mjs` idiom for parity) that is never called — cosmetic dead code.

## Changes summary

| File | Action | Description |
| --- | --- | --- |
| `infra/pi/web/static/js/expander.js` | created | Pure `expand(x, curve)` + `GROUP_CURVES` per-group tunable curves |
| `infra/pi/web/static/js/arkit-map.js` | modified | Expression params wrapped in their group's expander curve; lid/gaze untouched |
| `infra/pi/web/static/js/accents.js` | created | `createAccents({now})` — 3 Schmitt+sustain+eased discrete accents |
| `infra/pi/web/static/js/facial.js` | modified | `apply` layers accents over base map; `release` resets accents |
| `infra/pi/web/static/js/main.js` | modified | Construct `createAccents()` at the `createFacial` call site |
| `infra/pi/web/static/js/expander.test.mjs` | created | 32 assertions — curve endpoints/floor/gain/odd/clamp |
| `infra/pi/web/static/js/accents.test.mjs` | created | 22 assertions — threshold/sustain/flicker/release/isolation/no-mouth-open |
| `infra/pi/web/static/js/arkit-map.test.mjs` | modified | +6 assertions — mid-range amplification, endpoints preserved |

## Commits

- `32f2f6a` docs: add implementation plan
- `cb05498` docs: add head-tilt accent to plan
- `8308dc6` feat: add expander curve module
- `43c0857` feat: add discrete vtuber accent state machine
- `161036b` feat: apply expander curves in the arkit-live2d mapper
- `0f0dc80` feat: layer discrete accents into the facial controller
- `53fb2eb` test: add accent hysteresis unit tests
- `871aea3` test: add expander curve unit tests
