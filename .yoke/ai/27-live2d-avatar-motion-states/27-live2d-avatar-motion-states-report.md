# Report: 27-live2d-avatar-motion-states

**Plan:** `.yoke/ai/27-live2d-avatar-motion-states/27-live2d-avatar-motion-states-plan.md`
**Mode:** sub-agents
**Status:** ✅ complete

## Tasks

| #   | Task                                              | Status  | Commit    | Concerns |
| --- | ------------------------------------------------- | ------- | --------- | -------- |
| 1   | Download the Natori model into `static/models/`   | ✅ DONE | `57d6260` | —        |
| 2   | server.py — add image content type (`.png`)       | ✅ DONE | `50aa678` | —        |
| 3   | avatar.js + motion.js modules                     | ✅ DONE | `9139f95` | —        |
| 4   | Wire avatar into the shell (index.html/main/css)  | ✅ DONE | `fe85b96` | —        |
| 5   | Validation                                        | ✅ DONE | —         | —        |

## Post-implementation

| Step          | Status     | Commit |
| ------------- | ---------- | ------ |
| Validate      | ✅ pass    | —      |
| Documentation | ⏭️ skipped (no --update-docs) | — |
| Format        | ⏭️ N/A (no JS formatter/linter configured) | — |

## Reviews

All substantive tasks passed a combined spec+quality review with ✅ Approved (no issues):

- **Task 3 (avatar.js + motion.js)** — full spec parity; motion table exact; indices in range (`Idle` 0-2, `TapBody` 0-4); `avatar.js` reads PIXI only from `window.PIXI`, `motion.js` has zero DOM/global access; SDK-missing + model-load failures handled gracefully (return false, no throw); `dispose()` cleans up RO + app.
- **Task 4 (shell wiring)** — CDN script order correct (pixi → cubismcore → pixi-live2d-display, all before the module); `onAgentState` drives both the badge and `motion.setState`; avatar inits at page load (not gated on connect) with graceful degradation; attribution persistent; only 3 files changed, `room.js` untouched.
- Task 1 (model data) verified directly (see Validation); Task 2 is a one-line MIME addition validated by `py_compile` + diff.

## Validation

- `python3 -m py_compile infra/pi/web/server.py` ✅
- `node --check` on all 9 `static/js/*.js` (incl. new avatar.js, motion.js) ✅
- Model integrity: 25 files / 3.4 MB; `Natori.model3.json` valid JSON; motion groups `Idle` (3) + `TapBody` (5); 11 expressions; **no `Sound` refs** (no 404s) ✅
- `index.html` script order: livekit → pixi@6.5.10 → live2dcubismcore → pixi-live2d-display@0.4.0 → `main.js` (module) ✅
- Live2D attribution caption present in `index.html` ✅
- Live server smoke (agent venv):
  - `GET /static/models/natori/Natori.model3.json` → 200 `application/json` ✅
  - `GET /static/models/natori/Natori.moc3` → 200 `application/octet-stream` ✅
  - `GET /static/models/natori/Natori.2048/texture_00.png` → 200 `image/png` ✅
  - `GET /static/models/natori/motions/mtn_03.motion3.json` → 200 ✅
  - `GET /static/models/natori/exp/Smile.exp3.json` → 200 ✅
  - traversal `/static/../agent/.env` → 404 ✅

> **Visual render NOT verified headless.** Headless Chromium crashes on this Raspberry Pi (exit 124/133;
> never loads the page — a known ARM/Pi headless limitation). The avatar's actual on-screen rendering,
> motion switching, and fit-to-layout must be checked manually on the Pi's real (non-headless) kiosk
> Chromium, or any desktop browser pointed at the served page. All transport/asset/code validation is green.

## Design notes

- **Model = Natori** (official Live2D Cubism 4 sample, pinned `CubismWebSamples@b032ce2…`), chosen over Haru for 4× the animation surface (8 motions + 11 expressions). Free Live2D Sample Data license.
- **No free model has 4 motion groups** — every official sample uses the 2-group `Idle`+`Tap`/`TapBody` convention; 4+ groups exist only in (illegal) ripped commercial assets. The controller therefore maps states to specific `motion(group, index)` calls + distinct expressions:
  idle→`Idle`/0/Normal, listening→`Idle`/1/Normal, thinking→`TapBody`/0/Blushing, speaking→`TapBody`/2/Smile.
- **Mandatory attribution** rendered in the UI: *"This content uses sample data owned and copyrighted by Live2D Inc."* (Live2D Sample Data Terms; no design modification permitted).
- Motion is driven from the existing slice-1 `onAgentState` hook; `room.js` untouched. The `motion.setState` abstraction is the swap point for slice 3 (authoritative motion events).

## Changes summary

| File                                            | Action   | Description |
| ----------------------------------------------- | -------- | ----------- |
| `infra/pi/web/static/models/natori/**` (25)     | created  | Natori Cubism 4 model: model3/moc3/physics/pose/cdi, texture, 8 motions, 11 expressions. |
| `infra/pi/web/server.py`                         | modified | `.png` → `image/png` in the static content-type map. |
| `infra/pi/web/static/js/avatar.js`              | created  | PIXI + Live2D model lifecycle (init/playMotion/setExpression/dispose, responsive fit). |
| `infra/pi/web/static/js/motion.js`              | created  | Motion-controller: `lk.agent.state` → motion (group+index) + expression. |
| `infra/pi/web/index.html`                        | modified | 3 Live2D CDN scripts (ordered), avatar canvas host, loading hint, Live2D attribution. |
| `infra/pi/web/static/js/main.js`                | modified | Instantiate avatar + motion; init at load; drive `motion.setState` from `onAgentState`. |
| `infra/pi/web/static/css/styles.css`            | modified | Canvas fills `#avatar`; hint + attribution absolutely positioned; layout preserved. |

## Commits

- `6eca34a` #27 docs: add implementation plan
- `626b39c` #27 docs: switch model to Natori in plan
- `57d6260` #27 feat: add Natori Live2D model assets
- `50aa678` #27 feat: serve PNG textures with image/png MIME
- `9139f95` #27 feat: add avatar and motion-controller modules
- `fe85b96` #27 feat: mount Live2D avatar and drive motion from agent state

## Follow-ups for slice 3 (#28)

- Slice 3 swaps the motion *source* (authoritative agent motion events + LLM emotion tags) by calling the
  same `motion.setState`; Natori's 11 expressions map onto the emotion enum (neutral→Normal, happy→Smile,
  sad→Sad, surprised→Surprised, thinking→Blushing).
- The Cubism Core blob loads from Live2D's official CDN at runtime; if the kiosk must run offline, download
  it locally and serve from `static/`.
- Tune the per-state motion/expression mapping after a visual pass on the real display.
