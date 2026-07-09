# Report: 46-3d-face

**Plan:** `.yoke/ai/46-3d-face/46-3d-face-plan.md`
**Mode:** sub-agents (parallel)
**Status:** ✅ complete

3D browser face (Ready Player Me + three.js) driven 1:1 by the existing A2F ARKit
pipeline — additive page at `/face3d`, Live2D page untouched. ADR-0017 milestone 1.

## Tasks

| #   | Task                                             | Status  | Commit    | Concerns |
| --- | ------------------------------------------------ | ------- | --------- | -------- |
| 1   | Vendor three.js + GLTFLoader import-map assets   | ✅ DONE | `08f8395` | —        |
| 2   | Placeholder RPM `.glb` (ARKit-52 morphs)         | ✅ DONE | `1b7a2a4` | placeholder icosphere; real avatar is a 1-line path swap (DD-8) |
| 3   | Pure `arkit-rpm-map.js` + test                   | ✅ DONE | `bcf05b8` | —        |
| 4   | Pure `morph-apply.js` + test                     | ✅ DONE | `34d1d59` | —        |
| 5   | `face3d-renderer.js` (three.js scene/model/loop) | ✅ DONE | `cb99874`, fix `8956dbf` | camera framing is heuristic — tune when the real avatar lands |
| 6   | `face3d-sinks.js` (guarded facial + jaw mouth) + test | ✅ DONE | `2992691` | —   |
| 7   | `face3d.html` shell + `face3d-main.js` wiring    | ✅ DONE | `a57a082` | —        |
| 8   | `server.py` `/face3d` route                      | ✅ DONE | `27ff53e` | —        |
| 9   | Offline demo replayer + joy/anger fixtures       | ✅ DONE | `eb74c5e` | —        |
| 10  | ARKit-52 → RPM mapping-table doc                 | ✅ DONE | `74de534` | —        |
| 11  | Validation                                       | ✅ pass | —         | 2 ACs need on-device browser verification (see below) |

## Post-implementation

| Step          | Status     | Commit |
| ------------- | ---------- | ------ |
| Validate      | ✅ pass    | —      |
| Documentation | ⏭️ skipped | — (no `--update-docs`; T10 already produced the mapping-table doc) |
| Format        | ⏭️ N/A     | — (no formatter/linter config in repo) |

## Execution

All four groups ran per the plan DAG with a review pass after every task
(spec + quality, combined). One review finding surfaced and was fixed:

- **T5 review → Important (Spec):** `init()` could throw instead of returning
  `false` when WebGL context creation fails (renderer construction + canvas mount
  were outside the try/catch). That boolean is the caller's fallback signal, so it
  must cover WebGL failure. Fixed in `8956dbf` (whole setup now guarded) and
  re-verified. All other tasks approved on the first review pass.

- **Group 1** (parallel): T1, T2, T3, T4 → all ✅
- **Group 2** (parallel): T5, T6, T10 → all ✅ (T5 one fix)
- **Group 3** (sequential): T7 → T8 → T9 → all ✅

## Validation

Repo has no linter/build/formatter config — validation is the zero-dep Node ESM
test suite + syntax/serve checks.

- **Frontend test suites — 11/11 green** (`node <file>.test.mjs`):
  - New: `arkit-rpm-map.test.mjs` (20), `face3d-sinks.test.mjs` (17),
    `morph-apply.test.mjs` (10).
  - Existing, still green (Live2D path unaffected): `arkit-map` (32),
    `blendshapes` (29), `schedule` (75), `accents` (21), `expander` (32),
    `motion` (21), `mouth` (7), `lipsync` (9).
- **Syntax:** `node --check` OK on all new browser modules
  (`face3d-renderer/-sinks/-main/-demo`, `arkit-rpm-map`, `morph-apply`);
  `server.py` parses.
- **Serve smoke test:** `face3d.html` serves with the import map, vendored
  `three.module.js` path, `face3d-main.js`, LiveKit CDN, `#avatar` mount, and
  **0** pixi/cubism/live2d tags. All 11 static assets return 200 with correct
  sizes (three 1.30 MB, GLTFLoader 110 KB, BufferGeometryUtils 31 KB,
  `avatar.glb` 123 KB, joy/anger fixtures 465/485 KB — 221 frames each).
- **Guard proven (AC#5):** `face3d-sinks.test.mjs` case (b) reproduces the
  `blendshapes.js:45-46` sequence — a throwing renderer inside `facial.apply` does
  NOT propagate, and `mouth.ingestA2FFrame` still writes `jawOpen`.
- **Live2D untouched (AC#1):** `git diff origin/main..HEAD` touches none of
  `index.html`, `main.js`, `avatar.js`, `facial.js`, `mouth.js`, `arkit-map.js`.

### Acceptance criteria

| AC | Criterion | Result |
| -- | --------- | ------ |
| 1 | New `/face3d` route; Live2D page untouched & still served | ✅ route + shell added; Live2D diff empty |
| 2 | RPM avatar loads via self-hosted three.js, no CDN for the 3D stack | ✅ vendored + import map; assets serve 200 · visual render needs a browser (below) |
| 3 | `window.__a2fInject` frames animate the face 1:1; demoed with run-joy/anger | ⚠️ replayer + fixtures wired & unit-verified; **visible animation needs a WebGL browser** |
| 4 | ARKit-52 → RPM mapping verified + documented; unmapped listed | ✅ `docs/research/46-arkit-rpm-morph-map.md` (52 mapped + 16 Tongue* unmapped); unit-tested |
| 5 | A2F-only lipsync; a face-renderer throw can't stop mouth updates | ✅ no volume analyser on this page; guard proven by test |
| 6 | Live path: joins room, plays agent audio, face animates in sync | ⚠️ **needs the live stack (Pi + agent + Desktop TTS/A2F) + browser** |

**Two ACs (#3 visible animation, #6 live path) cannot be verified headlessly**
in this environment — no WebGL context, the `livekit` python module isn't
installed here, and there's no live agent/Desktop A2F. Everything they depend on
is in place and unit/serve-verified; they need a manual check on the Pi with a
real browser:
- `http://<pi>:8080/face3d?demo=joy` and `?demo=anger` → mouth + expression move.
- Connect on `/face3d` with the agent running → face animates from the live
  `voiceagent` stream in sync with audio.

## Changes summary

| File | Action | Description |
| ---- | ------ | ----------- |
| `infra/pi/web/static/js/vendor/three.module.js` | created | Pinned three.js r0.169.0 ESM build |
| `infra/pi/web/static/js/vendor/jsm/loaders/GLTFLoader.js` | created | GLTF loader (bare `three` import via import map) |
| `infra/pi/web/static/js/vendor/jsm/utils/BufferGeometryUtils.js` | created | GLTFLoader's jsm dependency |
| `infra/pi/web/static/models/rpm/avatar.glb` | created | Placeholder avatar, 52 camelCase ARKit morph targets |
| `infra/pi/web/static/js/arkit-rpm-map.js` (+test) | created | ARKit-52 PascalCase → RPM camelCase 1:1 map |
| `infra/pi/web/static/js/morph-apply.js` (+test) | created | Multi-mesh morph-influence apply helper |
| `infra/pi/web/static/js/face3d-renderer.js` | created | three.js scene/model/loop; `applyMorphs` seam |
| `infra/pi/web/static/js/face3d-sinks.js` (+test) | created | Guarded facial + jaw-mouth sinks (DD-3 split) |
| `infra/pi/web/face3d.html` | created | Page shell (import map, no Live2D tags) |
| `infra/pi/web/static/js/face3d-main.js` | created | Live2D-free entry wiring; hooks omit `lipsync` |
| `infra/pi/web/server.py` | modified | `/face3d` route (mirrors `/`) |
| `infra/pi/web/static/js/face3d-demo.js` | created | `?demo=joy\|anger` offline replayer |
| `infra/pi/web/static/demo/run-joy.json`, `run-anger.json` | created | Calibration capture fixtures (221 frames each) |
| `docs/research/46-arkit-rpm-morph-map.md` | created | Verified mapping table |

## Commits

- `08f8395` #46 feat(46-3d-face): vendor three.js and GLTFLoader for 3D face
- `34d1d59` #46 feat(46-3d-face): add morph-influence apply helper with test
- `bcf05b8` #46 feat(46-3d-face): add ARKit-to-RPM morph name mapping with test
- `1b7a2a4` #46 feat(46-3d-face): add placeholder avatar glb with ARKit-52 morph targets
- `cb99874` #46 feat(46-3d-face): add three.js face renderer with morph-target driving
- `2992691` #46 feat(46-3d-face): add guarded A2F face sinks with jaw/facial split and test
- `74de534` #46 docs(46-3d-face): document ARKit-52 to RPM morph-name mapping table
- `8956dbf` #46 fix(46-3d-face): guard WebGL/renderer setup in init so it returns false not throws
- `a57a082` #46 feat(46-3d-face): add 3D face page shell and entry wiring
- `27ff53e` #46 feat(46-3d-face): serve the 3D face page at /face3d
- `eb74c5e` #46 feat(46-3d-face): add offline A2F demo replayer with joy/anger fixtures
