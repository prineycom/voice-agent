// Live2D avatar: owns the PIXI application and Cubism-4 model lifecycle.
// Consumes CDN globals (window.PIXI v6, Live2DCubismCore, PIXI.live2d from
// pixi-live2d-display). Only `containerEl` is touched in the DOM; everything
// else is framework-free. Optional `log` mirrors the failure-logging style of
// the other harness modules.
export function createAvatar(containerEl, { log } = {}) {
  let app = null;
  let model = null;
  let ready = false;
  let ro = null;

  async function init() {
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
      model = await PIXI.live2d.Live2DModel.from('static/models/natori/Natori.model3.json');
    } catch (e) {
      log && log('загрузка модели не удалась: ' + e.message);
      return false;
    }

    app.stage.addChild(model);
    model.anchor.set(0.5, 0.5);

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

  function setExpression(name) {
    if (!ready || !model) return;
    try {
      model.expression(name);
    } catch (e) {
      log && log('установка выражения не удалась: ' + e.message);
    }
  }

  function dispose() {
    ro && ro.disconnect();
    try {
      app && app.destroy(true, { children: true });
    } catch (e) {
      log && log('освобождение ресурсов не удалось: ' + e.message);
    }
    app = null;
    model = null;
    ready = false;
    ro = null;
  }

  return {
    init,
    playMotion,
    setExpression,
    dispose,
    get ready() { return ready; },
  };
}
