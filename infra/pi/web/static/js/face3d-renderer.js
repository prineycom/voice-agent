// 3D face renderer: owns a three.js scene that loads a Ready-Player-Me-style
// .glb, mounts a WebGL canvas into the given container, gathers the model's
// morph-target meshes, frames a static camera on the head, and runs a render
// loop. The only sink into the rig is applyMorphs(map) — driven every A2F frame
// by a sibling module.
//
// The render loop decouples INPUT from DISPLAY: applyMorphs records per-morph
// TARGETS; each rendered frame eases the live influences toward those targets.
// Lipsync morphs (mouth/jaw/tongue) are written directly so speech stays crisp;
// expression morphs (brows/eyes/cheeks/nose) are smoothed so they glide instead
// of snapping between the ~30fps A2F frames. On top of the A2F signal we add
// procedural life the flat capture lacks: periodic eye blinks and a gentle idle
// head sway — both always on, so the face never looks frozen.
//
// Bare/addons specifiers resolve via an import map added in a later task; do NOT
// hardcode vendor paths here.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

// Single-line swap for the real avatar later (DD-8).
const MODEL_URL = '/static/models/rpm/avatar.glb';

// Expression smoothing time constant (ms). Larger = smoother/slower.
const SMOOTH_TAU_MS = 70;
// Procedural blink: a full close/open every BLINK_MIN..MAX ms, lasting BLINK_DUR.
const BLINK_MIN_MS = 2800;
const BLINK_MAX_MS = 6000;
const BLINK_DUR_MS = 160;
// Idle head sway amplitudes (radians) — small, so the head drifts, not bobbles.
const HEAD_YAW = 0.055;
const HEAD_PITCH = 0.035;
const HEAD_ROLL = 0.022;

// RPM renders some ARKit lip shapes far more strongly than the A2F signal
// intends — pucker/funnel/roll drive the "duck lips" and "sucked-in" look. Scale
// those back so speech reads naturally. jawOpen (the main open/close) and the
// smile/frown shapes are left at full strength.
const MORPH_GAIN = {
  // Root cause of the "upper lip covers the lower on б/п/м" artifact: A2F
  // over-emits the lip-ROLL channel across all voiced speech (MouthRollUpper
  // mean ~0.38, pins to 1.0 on bilabials). Rolling tucks the lips inward; at
  // those magnitudes RPM's mouthRollUpper curls the upper lip down over the
  // lower. The old Live2D pipeline never showed this because it drove the mouth
  // with ONLY open (ParamMouthOpenY) + form (ParamMouthForm) and discarded roll
  // entirely. Bilabial closure is already correct from jawOpen + mouthClose, so
  // we drop the unreliable roll channel rather than fight its magnitude.
  mouthRollUpper: 0,
  mouthRollLower: 0,
  // Pucker/funnel are the real "duck-lips / upper-lip-over-lower" driver: A2F
  // over-emits them and RPM pushes the lips forward into a pout. Verified by
  // headless render ablation — 0.5 was still pursed, ~0.2 reads natural.
  mouthPucker: 0.2,
  mouthFunnel: 0.2,
  mouthShrugUpper: 0.4,
  mouthShrugLower: 0.6,
};

// Hard per-morph ceilings, applied AFTER gain. `mouthClose` is the root cause of
// the "upper lip rolls over the lower on б/п/м" collapse: this RPM avatar's
// mouthClose morph is badly authored and drags the upper lip down past ~0.25,
// but A2F sends it up to 1.0 on bilabials. A gain can't both seal typical values
// and cap extremes, so we clamp: any mouthClose ≥ the cap renders as a natural
// gentle lip seal (verified by headless render) instead of collapsing. Live2D
// avoided this entirely by folding mouthClose into the jaw and never applying the
// morph; the cap is the 3D equivalent.
const MORPH_MAX = {
  mouthClose: 0.15,
};

// Lipsync morphs stay crisp (written directly); everything else is smoothed.
function isFastMorph(name) {
  return name.startsWith('mouth') || name.startsWith('jaw') || name.startsWith('tongue');
}

function nowMs() {
  return (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
}

export function createFaceRenderer(container, { log } = {}) {
  let renderer = null;
  let scene = null;
  let camera = null;
  let gltfRoot = null;
  let rafId = null;
  let ro = null;
  let ready = false;
  // Meshes carrying BOTH a morph dictionary and an influences array; collected
  // once on load and reused every frame (keep the loop allocation-light).
  let morphMeshes = [];

  // Latest per-morph targets from A2F (name -> value). The render loop eases the
  // live influences toward these; between A2F frames the last value simply holds.
  let targetMorphs = {};
  // Idle head sway drives this bone off its bind-pose orientation.
  let headBone = null;
  let headBaseQuat = null;
  // Animation clock + blink scheduler.
  let lastT = 0;
  let startT = 0;
  let nextBlinkAt = 0;
  let blinkStart = -1;
  const _euler = new THREE.Euler();
  const _quat = new THREE.Quaternion();

  function sizeToContainer() {
    if (!renderer || !camera) return;
    const w = container.clientWidth || 1;
    const h = container.clientHeight || 1;
    // updateStyle=true so the canvas CSS box matches the container; false would
    // leave the canvas at its raw buffer size (container×devicePixelRatio) and
    // overflow the stage, covering the header controls.
    renderer.setSize(w, h, true);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }

  // 0..1 blink amount; fires a quick sine close/open, then schedules the next.
  function proceduralBlink(t) {
    if (blinkStart < 0 && t >= nextBlinkAt) blinkStart = t;
    if (blinkStart >= 0) {
      const p = (t - blinkStart) / BLINK_DUR_MS;
      if (p >= 1) {
        blinkStart = -1;
        nextBlinkAt = t + BLINK_MIN_MS + Math.random() * (BLINK_MAX_MS - BLINK_MIN_MS);
        return 0;
      }
      return Math.sin(p * Math.PI); // 0 -> 1 -> 0 across the blink
    }
    return 0;
  }

  function animate(t) {
    const dt = Math.min(100, t - lastT);
    lastT = t;
    const k = 1 - Math.exp(-dt / SMOOTH_TAU_MS); // frame-rate-independent ease

    // 1) Ease/write morph influences toward their targets.
    for (const mesh of morphMeshes) {
      const dict = mesh.morphTargetDictionary;
      const inf = mesh.morphTargetInfluences;
      for (const name in dict) {
        const target = targetMorphs[name];
        if (target === undefined) continue;
        const idx = dict[name];
        inf[idx] = isFastMorph(name) ? target : inf[idx] + (target - inf[idx]) * k;
      }
    }

    // 2) Procedural blink — max()'d over the A2F blink so both still read.
    const b = proceduralBlink(t);
    if (b > 0) {
      for (const mesh of morphMeshes) {
        const dict = mesh.morphTargetDictionary;
        const inf = mesh.morphTargetInfluences;
        for (const bn of ['eyeBlinkLeft', 'eyeBlinkRight']) {
          const idx = dict[bn];
          if (idx !== undefined && b > inf[idx]) inf[idx] = b;
        }
      }
    }

    // 3) Idle head sway — small drift off the bind pose on three slow sines.
    if (headBone && headBaseQuat) {
      const s = (t - startT) / 1000;
      _euler.set(
        Math.sin(s * 0.62 + 1.3) * HEAD_PITCH,
        Math.sin(s * 0.47) * HEAD_YAW,
        Math.sin(s * 0.35 + 2.1) * HEAD_ROLL,
        'XYZ',
      );
      _quat.setFromEuler(_euler);
      headBone.quaternion.copy(headBaseQuat).multiply(_quat);
    }
  }

  function renderLoop() {
    rafId = requestAnimationFrame(renderLoop);
    if (!renderer || !scene || !camera) return;
    animate(nowMs());
    renderer.render(scene, camera);
  }

  function teardown() {
    if (rafId != null) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
    ro && ro.disconnect();
    ro = null;

    // Dispose geometries/materials (and their textures) before dropping the renderer.
    // material.dispose() does NOT free GPU textures, so we dispose them explicitly.
    const disposeMaterial = (m) => {
      if (!m) return;
      for (const key in m) {
        const value = m[key];
        if (value && value.isTexture && value.dispose) value.dispose();
      }
      if (m.dispose) m.dispose();
    };
    if (scene) {
      scene.traverse((obj) => {
        if (obj.geometry && obj.geometry.dispose) obj.geometry.dispose();
        const mat = obj.material;
        if (mat) {
          if (Array.isArray(mat)) mat.forEach(disposeMaterial);
          else disposeMaterial(mat);
        }
      });
    }

    const view = renderer && renderer.domElement;
    try {
      renderer && renderer.dispose();
    } catch (e) {
      log && log('освобождение WebGL не удалось: ' + e.message);
    }
    if (view && view.parentNode) view.parentNode.removeChild(view);

    renderer = null;
    scene = null;
    camera = null;
    gltfRoot = null;
    morphMeshes = [];
    targetMorphs = {};
    headBone = null;
    headBaseQuat = null;
    ready = false;
  }

  async function init() {
    if (renderer) return ready;
    if (!container) {
      log && log('3D face: контейнер не найден');
      return false;
    }

    const w = container.clientWidth || 1;
    const h = container.clientHeight || 1;

    // Wrap the WHOLE setup — scene/camera build, WebGLRenderer construction (which
    // throws "Error creating WebGL context" on a GPU-less/WebGL-disabled browser),
    // canvas mount, and model load — so ANY failure cleans up, logs, and resolves
    // false rather than rejecting. That boolean is the caller's fallback signal.
    try {
      scene = new THREE.Scene();
      scene.background = null; // transparent — let the page/container show through

      camera = new THREE.PerspectiveCamera(30, w / h, 0.01, 100);

      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
      renderer.setPixelRatio(window.devicePixelRatio || 1);
      // updateStyle=true — size the canvas CSS box to the container (see sizeToContainer).
      renderer.setSize(w, h, true);
      container.appendChild(renderer.domElement);

      // Lighting: hemisphere fill so morph deltas read on both sides + a directional
      // key so the head has form and shadowed morph motion is visible.
      const hemi = new THREE.HemisphereLight(0xffffff, 0x444455, 1.4);
      scene.add(hemi);
      const key = new THREE.DirectionalLight(0xffffff, 1.6);
      key.position.set(0.5, 1.0, 1.0);
      scene.add(key);

      const loader = new GLTFLoader();
      const gltf = await loader.loadAsync(MODEL_URL);

      gltfRoot = gltf.scene || (gltf.scenes && gltf.scenes[0]);
      if (!gltfRoot) {
        log && log('3D-модель без сцены');
        teardown();
        return false;
      }
      scene.add(gltfRoot);

      // Collect every mesh that can be morph-driven (head, teeth, eyes, ...).
      morphMeshes = [];
      gltfRoot.traverse((obj) => {
        if (obj.morphTargetDictionary && obj.morphTargetInfluences) {
          morphMeshes.push(obj);
        }
      });
      if (morphMeshes.length === 0) {
        log && log('3D-модель без morph-целей — лицо не будет анимировано');
      }

      // Grab the head bone for the idle sway (RPM rigs name it exactly "Head";
      // fall back to any head-ish bone). Absent on the placeholder sphere — the
      // sway then simply no-ops.
      headBone = null;
      gltfRoot.traverse((o) => { if (!headBone && o.isBone && /^head$/i.test(o.name)) headBone = o; });
      if (!headBone) gltfRoot.traverse((o) => { if (!headBone && o.isBone && /head/i.test(o.name)) headBone = o; });
      headBaseQuat = headBone ? headBone.quaternion.clone() : null;

      // Frame the camera on the HEAD, not the whole model — a full-body RPM avatar's
      // bounding box would shrink the face to a distant speck. Prefer the head mesh
      // (RPM's `Wolf3D_Head`); fall back to the whole model, which is correct for the
      // placeholder sphere (it has no head-named mesh, so it frames the sphere).
      scene.updateMatrixWorld(true);
      let focus = null;
      gltfRoot.traverse((o) => { if (!focus && o.geometry && /head/i.test(o.name)) focus = o; });
      const box = new THREE.Box3().setFromObject(focus || gltfRoot);
      if (box.isEmpty()) {
        // Degenerate bounds — sane head-height framing.
        camera.position.set(0, 1.6, 0.6);
        camera.lookAt(0, 1.6, 0);
      } else {
        const size = new THREE.Vector3();
        const center = new THREE.Vector3();
        box.getSize(size);
        box.getCenter(center);
        // Fit the head's larger planar extent in the FOV with headroom, and sit the
        // camera in front (+Z — the side RPM avatars face) at head level.
        const extent = Math.max(size.x, size.y, 0.001);
        const fov = (camera.fov * Math.PI) / 180;
        const dist = (extent / 2 / Math.tan(fov / 2)) * 1.5 + size.z;
        camera.position.set(center.x, center.y, center.z + dist);
        camera.lookAt(center.x, center.y, center.z);
      }
      camera.updateProjectionMatrix();

      ro = new ResizeObserver(() => sizeToContainer());
      ro.observe(container);
    } catch (e) {
      log && log('инициализация 3D-лица не удалась: ' + (e && e.message ? e.message : e));
      teardown();
      return false;
    }

    // Start the animation clock and schedule the first blink shortly after load.
    targetMorphs = {};
    startT = lastT = nowMs();
    nextBlinkAt = startT + 1200;
    blinkStart = -1;

    // Build marker so we can confirm from the on-page Лог panel WHICH renderer
    // code is live (rules out browser caching when diagnosing visual changes).
    log && log('face3d B9 · mouthCloseMax=' + MORPH_MAX.mouthClose + ' pucker=' + MORPH_GAIN.mouthPucker + ' rollU=' + (MORPH_GAIN.mouthRollUpper ?? 1) + ' · blink+sway ON');

    ready = true;
    renderLoop();
    return true;
  }

  // Per-frame sink: record targets (with per-morph gain); the render loop eases them.
  function applyMorphs(map) {
    if (!ready || !map) return;
    // `?? 1` not `|| 1`: a gain of 0 (roll morphs) is falsy and `|| 1` would
    // silently restore it to full strength. Then clamp to any per-morph ceiling.
    for (const name in map) {
      let v = map[name] * (MORPH_GAIN[name] ?? 1);
      const mx = MORPH_MAX[name];
      if (mx !== undefined && v > mx) v = mx;
      targetMorphs[name] = v;
    }
  }

  return {
    init,
    applyMorphs,
    dispose: teardown,
    get ready() { return ready; },
  };
}
