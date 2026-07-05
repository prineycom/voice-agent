// Live2D avatar: owns the PIXI application and Cubism-4 model lifecycle.
// Consumes CDN globals (window.PIXI v6, Live2DCubismCore, PIXI.live2d from
// pixi-live2d-display). Only `containerEl` is touched in the DOM; everything
// else is framework-free. Optional `log` mirrors the failure-logging style of
// the other harness modules.
//
// Which model to load comes from the active avatar profile (avatar-config.js),
// so swapping/reverting the avatar is a one-line config change, not a code edit.
import { activeProfile } from './avatar-config.js';

export function createAvatar(containerEl, { log } = {}) {
  let app = null;
  let model = null;
  let ready = false;
  let ro = null;
  let mouthOpen = 0;
  let faceParams = null;

  function teardown() {
    ro && ro.disconnect();
    const view = app && app.view;
    try {
      app && app.destroy(true, { children: true });
    } catch (e) {
      log && log('освобождение ресурсов не удалось: ' + e.message);
    }
    if (view && view.parentNode) {
      view.parentNode.removeChild(view);
    }
    app = null;
    model = null;
    ro = null;
    ready = false;
    mouthOpen = 0;
    faceParams = null;
  }

  async function init() {
    if (app) return ready;
    if (!(window.PIXI && window.PIXI.live2d)) {
      log && log('Live2D SDK не загружен');
      return false;
    }
    const PIXI = window.PIXI;

    try {
      PIXI.live2d.Live2DModel.registerTicker(PIXI.Ticker);
    } catch (e) {
      log && log('registerTicker не удался: ' + e.message);
    }

    app = new PIXI.Application({
      resizeTo: containerEl,
      backgroundAlpha: 0,
      antialias: true,
      autoDensity: true,
      resolution: window.devicePixelRatio || 1,
    });
    containerEl.appendChild(app.view);

    try {
      model = await PIXI.live2d.Live2DModel.from(activeProfile.model);
    } catch (e) {
      log && log('загрузка модели не удалась: ' + e.message);
      teardown();
      return false;
    }

    app.stage.addChild(model);
    model.anchor.set(0.5, 0.5);

    // Volume-driven lip-sync. `beforeModelUpdate` fires after the motion/physics
    // pass and just before the frame commits, so writing an absolute value here
    // makes us the last writer and overrides the idle motion's mouth keyframes.
    // (setParameterValueById is absolute; addParameterValueById would be additive.)
    // Deliberate: this also pins the mouth to `mouthOpen` (0 when no lip-sync is
    // active), suppressing the idle motion's baked mouth movement so a silent
    // agent reads as closed-mouthed rather than appearing to talk silently.
    model.internalModel.on('beforeModelUpdate', () => {
      try {
        model.internalModel.coreModel.setParameterValueById('ParamMouthOpenY', mouthOpen);
      } catch (e) { /* unknown param id silently no-ops */ }
      if (faceParams) {
        for (const id in faceParams) {
          try { model.internalModel.coreModel.setParameterValueById(id, faceParams[id]); }
          catch (e) { /* unknown param id silently no-ops */ }
        }
      }
    });

    const layout = () => {
      const { width, height } = app.renderer.screen;
      const scale = Math.min(width / model.internalModel.width, height / model.internalModel.height) * 0.9;
      model.scale.set(scale);
      model.x = width / 2;
      model.y = height / 2;
    };
    layout();

    ro = new ResizeObserver(() => { if (model) layout(); });
    ro.observe(containerEl);

    ready = true;
    return true;
  }

  function playMotion(group, index) {
    if (!ready || !model) return;
    try {
      model.motion(group, index, window.PIXI.live2d.MotionPriority.FORCE);
    } catch (e) {
      log && log('воспроизведение движения не удалось: ' + e.message);
    }
  }

  // External lip-sync driver pushes a 0..1 mouth-open value applied every frame.
  function setMouthOpen(v) {
    mouthOpen = Math.max(0, Math.min(1, Number(v) || 0));
  }

  // Absolute face-param sink for A2F-driven facial animation. A map of
  // {paramId: value} is pinned every frame in the beforeModelUpdate hook so it
  // overrides idle-motion keyframes; null releases the params so Live2D's own
  // auto-blink / idle motion runs (don't hold a stale pose between turns).
  function setFaceParams(map) { faceParams = map || null; }

  function setExpression(name) {
    if (!ready || !model) return;
    // Some models (e.g. Hiyori) ship no expression files; the profile maps
    // their expressions to null, which we treat as an intentional no-op.
    if (name == null) return;
    try {
      model.expression(name);
    } catch (e) {
      log && log('установка выражения не удалась: ' + e.message);
    }
  }

  return {
    init,
    playMotion,
    setExpression,
    setMouthOpen,
    setFaceParams,
    dispose: teardown,
    get ready() { return ready; },
  };
}
