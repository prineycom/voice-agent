# Code Review: 46-3d-face

## Summary

### Context and goal

Issue #46 (ADR-0017 milestone 1) adds a self-hosted three.js / Ready-Player-Me
3D face on a NEW page `/face3d`, additive to the untouched Live2D page. It drives
glTF morph targets 1:1 from the existing A2F ARKit stream, reusing
`blendshapes.js`/`schedule.js`/`room.js`/`transcript.js`/`ops.js` unchanged and
swapping only the renderer + its sinks. A2F-only lipsync (no volume fallback), a
placeholder `.glb`, and an offline demo replayer for run-joy/run-anger.

### Key code areas for review

1. **`arkit-rpm-map.js:arkitToRpmMorphs()`** — the PascalCase→camelCase 1:1
   bridge; verified byte-for-byte against `arkit.py` ARKIT_52 + TONGUE_16.
2. **`face3d-sinks.js:createFaceSinks()`** — the retro guard: every sink method
   is individually try/catch-wrapped; the DD-3 split (facial writes 51, mouth
   writes only `jawOpen`) means no morph is double-written per frame.
3. **`face3d-renderer.js:init()/teardown()/applyMorphs()`** — whole-setup guard
   returning `false`, rAF loop, `ready` gate, ResizeObserver, dispose.
4. **`face3d-main.js`** — wiring vs `main.js`: all 19 DOM ids present in
   `face3d.html`, `lipsync` hook omitted, `motion` a no-op stub,
   `blendshapes.audioStopped()` preserved.
5. **`face3d-demo.js:startFaceDemo()`** — fetch + per-frame `__a2fInject`
   scheduling + `{done:true}` tail; now cancellable.
6. **`server.py` /face3d route** — exact-string router, fixed-file read.

### Complex decisions

1. **DD-3 jaw ownership** (`face3d-sinks.js`) — facial uses
   `arkitToRpmMorphs(..,{excludeJaw:true})`, mouth writes raw `clamp01(JawOpen)`.
   No key written twice; empty-overlap asserted by test.
2. **DD-4 guard in the sink, not in blendshapes.js** — every sink method
   self-swallows so a WebGL/morph throw can never reach past `facial.apply` and
   freeze `mouth.ingestA2FFrame`. Retro bug closed.
3. **A2F-only, no volume fallback** — `lipsync` key omitted from `hooks`;
   `room.js` guards `if (opts.lipsync)`, so no room.js edit needed.
4. **Import-map self-hosting** (`face3d.html`) — importmap precedes the module
   script; `three/addons/`→`jsm/` resolves GLTFLoader's relative import.

### Questions for the reviewer

1. Real RPM avatar sourcing + camera-framing tune-up (deferred to a follow-up —
   the renderer is asset-agnostic; `.glb` path is a one-line swap).
2. `renderer.dispose()` is only wired to `beforeunload` today; milestone 2's
   in-page renderer swap will exercise the (now texture-complete) teardown.

### Risks and impact

- Low blast radius: net-new files + a 1-branch server route; no reused module is
  modified (`/` Live2D path untouched, confirmed by diff).
- Main runtime risk (WebGL unavailable) is handled — `init()` resolves `false`,
  the mouth still runs guarded, and the hint now shows an error state.

### Tests and manual checks

**Auto-tests:** 11/11 frontend Node suites green — new: `arkit-rpm-map` (20),
`face3d-sinks` (17), `morph-apply` (10); existing Live2D suites unaffected.

**Manual scenarios (need a WebGL browser on the tailnet):**
1. `https://ai.priney.com/face3d?demo=joy` / `?demo=anger` → mouth + expression
   animate 1:1.
2. Connect on `/face3d` with the agent running → face animates from the live
   `voiceagent` stream in sync with audio (demo auto-stops on Connect).

### Out of scope

- Vendored three.js/GLTFLoader, the placeholder `.glb`, copied demo fixtures —
  reviewed only for integrity, not internals.
- Emotion-source flatness (monotone TTS → flat A2E) — separate deferred track.
- Procedural idle head/gaze + `?facedebug` parity — milestones 2/3.

## Commits

| Hash      | Description |
| --------- | ----------- |
| `08f8395` | feat(46-3d-face): vendor three.js and GLTFLoader |
| `34d1d59` | feat(46-3d-face): add morph-influence apply helper with test |
| `bcf05b8` | feat(46-3d-face): add ARKit-to-RPM morph name mapping with test |
| `1b7a2a4` | feat(46-3d-face): add placeholder avatar glb with ARKit-52 morphs |
| `cb99874` | feat(46-3d-face): add three.js face renderer |
| `2992691` | feat(46-3d-face): add guarded A2F face sinks with jaw/facial split |
| `74de534` | docs(46-3d-face): document ARKit-52 → RPM morph-name mapping |
| `8956dbf` | fix(46-3d-face): guard WebGL/renderer setup in init |
| `a57a082` | feat(46-3d-face): add 3D face page shell and entry wiring |
| `27ff53e` | feat(46-3d-face): serve the 3D face page at /face3d |
| `eb74c5e` | feat(46-3d-face): add offline A2F demo replayer with fixtures |
| `a2af0d1` | docs(46-3d-face): add execution report |
| `8e55ec5` | fix(46-3d-face): fix 6 review issues |

## Changed Files

| File | Description |
| ---- | ----------- |
| `static/js/vendor/**` (three.module.js, GLTFLoader.js, BufferGeometryUtils.js) | Vendored three.js r0.169 + GLTF loader (self-hosted) |
| `static/models/rpm/avatar.glb` | Placeholder avatar, 52 camelCase ARKit morph targets |
| `static/js/arkit-rpm-map.js` (+test) | ARKit-52 → RPM camelCase 1:1 map |
| `static/js/morph-apply.js` (+test) | Multi-mesh morph-influence apply helper |
| `static/js/face3d-renderer.js` | three.js scene/loop; texture-complete teardown |
| `static/js/face3d-sinks.js` (+test) | Guarded facial + jaw-mouth sinks (DD-3 split) |
| `face3d.html` | Page shell (import map, no Live2D tags) |
| `static/js/face3d-main.js` | Live2D-free entry wiring; error-hint + demo-stop-on-connect |
| `server.py` | `/face3d` route (mirrors `/`) |
| `static/js/face3d-demo.js` | Cancellable `?demo=joy\|anger` offline replayer |
| `static/demo/run-joy.json`, `run-anger.json` | Calibration fixtures (221 frames) |
| `docs/research/46-arkit-rpm-morph-map.md` | Verified mapping table |

## Issues Found

| Severity | Score | Category | File:line | Description |
| -------- | ----- | -------- | --------- | ----------- |
| Minor | 35 | performance | `face3d-renderer.js` teardown | `material.dispose()` didn't free textures — VRAM leak on a mid-session dispose (M2) |
| Minor | 30 | quality | `face3d-demo.js` | ≤221 `setTimeout`s not cancellable — demo + live stream fight over `__a2fInject` |
| Minor | 20 | quality | `face3d-sinks.js` `beginA2FStream()` | Empty body wrapped in try/catch — dead guard |
| Minor | 18 | quality | `face3d-main.js` init-false | Permanent "загрузка аватара…" hint on WebGL/load failure |
| Minor | 12 | style | renderer vs sinks/demo | Log language inconsistent (Russian vs English) within the feature |
| Minor | 10 | quality | `face3d-main.js:64` | Unreachable `.catch` on `renderer.init()` (init never rejects) |

## Fixed Issues

| Issue | Commit | Description |
| ----- | ------ | ----------- |
| Texture VRAM leak on dispose | `8e55ec5` | `disposeMaterial` helper disposes every `isTexture` property before the material |
| Uncancellable demo replay | `8e55ec5` | `startFaceDemo` returns `{stop()}` clearing all timers; `doConnect` calls it before `room.connect` |
| Dead try/catch on beginA2FStream | `8e55ec5` | Removed the pointless guard; method stays an explicit no-op |
| Permanent loading hint on failure | `8e55ec5` | On `!ok`, hint text → "не удалось загрузить 3D-аватар" |
| Inconsistent log language | `8e55ec5` | Sinks + demo log strings translated to Russian (house convention) |
| Unreachable `.catch` | `8e55ec5` | Removed; the false branch is handled in `.then` |

## Skipped Issues

**All found issues were fixed.**

## Recommendations

- The retro guard is airtight and unit-proven; keep the "guard lives in the sink,
  blendshapes.js stays untouched" invariant when milestone 2 adds head/gaze.
- Milestone 2 will finally exercise `renderer.dispose()` (in-page renderer swap) —
  the teardown is now texture-complete, so that path is safe.
- Two acceptance criteria (visible animation, live path) remain **browser/live-stack
  manual checks** — both are now reachable at `https://ai.priney.com/face3d`
  (tailnet-only). Verify on-device before closing the issue.
- Real RPM avatar + camera framing are a deliberate follow-up; the renderer is
  asset-agnostic (one-line `.glb` path swap).
