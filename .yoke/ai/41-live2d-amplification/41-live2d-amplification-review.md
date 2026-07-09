# Code Review: 41-live2d-amplification

## Summary

### Context and goal

Issue #41 replaces the linear 52→12 ARKit→Live2D mapper with (a) nonlinear per-group
expander curves (`expander.js`) applied inside `arkit-map.js`, and (b) a discrete
three-accent Schmitt/sustain/envelope state machine (`accents.js`) layered over the base
map via `Object.assign` in `facial.js`. `main.js` wires one `createAccents()` instance into
`createFacial`. Goal: make the A2F-driven face read as expressive on the anime avatar.

### Key code areas for review

1. **`accents.js` `createAccents`/`step`** — the whole anti-flicker contract (Schmitt +
   sustain + envelope + key omission) lives here; a subtle bug here freezes, snaps, or leaks
   params.
2. **`accents.js` head-tilt (`release: 'ease-to-zero'`)** — the only non-base-owned accent
   (`ParamAngleZ`); its decay-then-omit release is what keeps the head from snapping against
   motion/physics.
3. **`expander.js` `expand`** — endpoint/parity/monotonicity guarantees that keep the
   existing `arkit-map.test.mjs` assertions green while amplifying the mid-range.
4. **`facial.js` `apply`/`release`** — accent-over-base layering order and `reset()` on
   release.

### Complex decisions

1. **Layering over the absolute last-writer sink** (`facial.js:15`) — accents `Object.assign`
   over a fresh base-map object each frame, so "wide eyes past 1.0" overrides the base clamp
   without editing the pure mapper. Correct, but it means an accent that duplicates a
   base-driven signal can transiently *undercut* it during envelope ramp — the source of the
   fixed Important issue.
2. **Envelope always eases from `env=0` at latch** (`accents.js:116-118`) — a uniform
   pop-in model; the eye-open target is special-cased to interpolate from 1, and (after the
   fix) joy-squint no longer re-drives the base-owned `ParamMouthForm`.

### Questions for the reviewer

1. On a *moderate* sustained smile (0.6–0.7), the base expander now solely owns
   `ParamMouthForm` (~0.7–0.8). Is that read strong enough, or is a glitch-free
   base-value-seeded mouth boost worth a follow-up? (The flat-1.0 accent push was dropped to
   kill the dip.)
2. The amazement brow-pop (`ParamBrowLY/RY`) shares the same override-from-0 pattern; it was
   judged low-risk (fast 120 ms accent, brows less central) and left as-is — acceptable?

### Risks and impact

- Visual glitch class (accent undercutting a base-driven param during ramp) — resolved for
  the prominent mouth case; noted as low-risk for brows.
- No mutation/aliasing risk: `arkitToLive2D` returns a fresh object before `Object.assign`.
- `main.js`/`facial.js` wiring is a single instantiation, not per-frame — no leak.

### Tests and manual checks

**Auto-tests:** `node --test infra/pi/web/static/js/*.test.mjs` — 8 files green. Coverage
now includes idle omission, sustain-gate timing, Schmitt no-flicker band, omit-not-zero on
release, eye-open floor-at-1, head-tilt monotone decay + omission, `reset()`, multi-accent
isolation, the no-mouth-open invariant, and (added in fixes) end-to-end expander wiring for
`ParamEyeLSmile`/`ParamCheek`.

**Manual scenarios:**
1. Emotional reply with `?facedebug=1` vs `?lipsync=volume` → A2F face obviously more alive
   (user perceived-effect acceptance — the one open gate).
2. Strong smile onset → no "un-smile" flicker on the mouth (fixed).
3. Barge-in / turn end → face releases to idle; head-tilt eases down without stuttering
   idle head-sway.

### Out of scope

- `.yoke/ai/**` plan/report/review markdown (artifacts).
- `schedule.js`, `mouth.js`, `blendshapes.js`, `facedebug.js` — untouched (confirmed via
  `git diff --stat`); the A2F/volume provider split and `?lipsync=volume` are intact.
- Base-value-seeded mouth boost, head-angle X accents, blush/sparkle anime accents — future.

## Commits

| Hash      | Description |
| --------- | ----------- |
| `32f2f6a` | docs: add implementation plan |
| `cb05498` | docs: add head-tilt accent to plan |
| `8308dc6` | feat: add expander curve module |
| `43c0857` | feat: add discrete vtuber accent state machine |
| `161036b` | feat: apply expander curves in the arkit-live2d mapper |
| `0f0dc80` | feat: layer discrete accents into the facial controller |
| `53fb2eb` | test: add accent hysteresis unit tests |
| `871aea3` | test: add expander curve unit tests |
| `5b92edc` | docs: add execution report |
| `4ac9a5d` | fix: fix 4 review issues |

## Changed Files

| File | +/- | Description |
| --- | --- | --- |
| `infra/pi/web/static/js/expander.js` | +44 | Pure `expand(x, curve)` + per-group `GROUP_CURVES` |
| `infra/pi/web/static/js/arkit-map.js` | +25/-13 | Expression params wrapped in group expander curves |
| `infra/pi/web/static/js/accents.js` | +151 | 3-accent Schmitt/sustain/eased state machine |
| `infra/pi/web/static/js/facial.js` | +8/-2 | Layer accents over base map; reset on release |
| `infra/pi/web/static/js/main.js` | +3/-1 | Wire `createAccents()` into `createFacial` |
| `infra/pi/web/static/js/expander.test.mjs` | +67 | Curve unit tests (32 assertions) |
| `infra/pi/web/static/js/accents.test.mjs` | +160 | Accent hysteresis unit tests |
| `infra/pi/web/static/js/arkit-map.test.mjs` | +23 | Mapper amplification assertions (24→32) |

## Issues Found

| Severity  | Score | Category | File:line | Description |
| --------- | ----- | -------- | --------- | ----------- |
| Important | 60 | bugs | `accents.js:46` | joy-squint re-drove base-owned `ParamMouthForm` from `env=0`, causing a visible dip-then-ramp "un-smile" flicker at latch |
| Minor | 30 | tests | `arkit-map.test.mjs` | expander integration untested for `ParamEyeLSmile`/`ParamCheek` — a wrong-curve slip would pass silently |
| Minor | 20 | documentation | `accents.js:28` | `ALPHA=0.3` comment implied it should equal mouth.js's 0.5 lerp |
| Minor | 15 | style | `accents.js:111-112` | `sinceAbove` reset placed outside its branch, reading like dead code |

## Fixed Issues

| Issue | Commit | Description |
| ----- | ------ | ----------- |
| joy-squint mouth-form flicker | `4ac9a5d` | Dropped `ParamMouthForm` from joy-squint's target — base expander already drives it; the eye-smile crinkle (base value low, no dip) is the real accent. Re-pointed the now-vacuous release-omission test assertion. |
| Untested eyes/cheeks expander wiring | `4ac9a5d` | Added integration assertions (mid-squint/mid-cheek amplify, tiny compresses, neutral +0); `arkit-map.test.mjs` 24→32 assertions |
| Misleading ALPHA comment | `4ac9a5d` | Reworded to "same one-pole idiom as mouth.js (0.5), tuned slower (0.3) for accent pacing" |
| `sinceAbove` reset placement | `4ac9a5d` | Moved into the deactivation branch; deleted the redundant trailing check (behavior-preserving) |

## Skipped Issues

**All found issues were fixed.**

## Recommendations

- **Land, then do the live perceived-effect check** — the one remaining acceptance gate:
  an emotional reply on `https://ai.priney.com` with `?facedebug=1` next to `?lipsync=volume`.
  Requires the frontend to be deployed/reloaded first.
- Tuning knobs are data: `GROUP_CURVES` (`expander.js`) for per-group gain/floor and the
  `ACCENTS` descriptor thresholds/targets (`accents.js`). Adjust without code changes if
  accents read too weak/strong.
- Cosmetic (recorded, not fixed): `accents.test.mjs` keeps an unused `approx` counter helper
  copied for idiom parity.
- Follow-ups if desired: base-value-seeded mouth boost (glitch-free full-smile push on
  moderate smiles), and applying the same undercut analysis to the amazement brow-pop.
