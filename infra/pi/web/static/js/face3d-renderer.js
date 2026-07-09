// 3D face renderer: owns a three.js scene that loads a Ready-Player-Me-style
// .glb, mounts a WebGL canvas into the given container, gathers the model's
// morph-target meshes, frames a static camera on the head, and runs a render
// loop. The only sink into the rig is applyMorphs(map) — driven every A2F frame
// by a sibling module — which pushes ARKit-style blendshape influences through
// the pure morph-apply helper.
//
// Bare/addons specifiers resolve via an import map added in a later task; do NOT
// hardcode vendor paths here.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { applyMorphInfluences } from './morph-apply.js';

// Single-line swap for the real avatar later (DD-8). Placeholder today.
const MODEL_URL = '/static/models/rpm/avatar.glb';

export function createFaceRenderer(container, { log } = {}) {
  let renderer = null;
  let scene = null;
  let camera = null;
  let gltfRoot = null;
  let rafId = null;
  let ro = null;
  let ready = false;
  // Meshes carrying BOTH a morph dictionary and an influences array; collected
  // once on load and reused every frame (keep applyMorphs allocation-light).
  let morphMeshes = [];

  function sizeToContainer() {
    if (!renderer || !camera) return;
    const w = container.clientWidth || 1;
    const h = container.clientHeight || 1;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }

  function renderLoop() {
    rafId = requestAnimationFrame(renderLoop);
    if (renderer && scene && camera) renderer.render(scene, camera);
  }

  function teardown() {
    if (rafId != null) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
    ro && ro.disconnect();
    ro = null;

    // Dispose geometries/materials before dropping the renderer.
    if (scene) {
      scene.traverse((obj) => {
        if (obj.geometry && obj.geometry.dispose) obj.geometry.dispose();
        const mat = obj.material;
        if (mat) {
          if (Array.isArray(mat)) mat.forEach((m) => m && m.dispose && m.dispose());
          else if (mat.dispose) mat.dispose();
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

    scene = new THREE.Scene();
    scene.background = null; // transparent — let the page/container show through

    camera = new THREE.PerspectiveCamera(30, w / h, 0.01, 100);

    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(window.devicePixelRatio || 1);
    renderer.setSize(w, h, false);
    container.appendChild(renderer.domElement);

    // Lighting: hemisphere fill so morph deltas read on both sides + a directional
    // key so the head has form and shadowed morph motion is visible.
    const hemi = new THREE.HemisphereLight(0xffffff, 0x444455, 1.4);
    scene.add(hemi);
    const key = new THREE.DirectionalLight(0xffffff, 1.6);
    key.position.set(0.5, 1.0, 1.0);
    scene.add(key);

    let gltf;
    try {
      const loader = new GLTFLoader();
      gltf = await loader.loadAsync(MODEL_URL);
    } catch (e) {
      log && log('загрузка 3D-модели не удалась: ' + (e && e.message ? e.message : e));
      teardown();
      return false;
    }

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

    // Frame a fixed camera on the head. Use the model's bounding box so the
    // static placeholder (a small sphere near origin) and a real head-height RPM
    // mesh both end up filling the view. We aim at the top portion of the box
    // (roughly where a head sits) and pull the camera back to fit vertically.
    const box = new THREE.Box3().setFromObject(gltfRoot);
    if (box.isEmpty()) {
      // Degenerate bounds — fall back to a sane head-height framing.
      camera.position.set(0, 1.6, 0.6);
      camera.lookAt(0, 1.6, 0);
    } else {
      const size = new THREE.Vector3();
      const center = new THREE.Vector3();
      box.getSize(size);
      box.getCenter(center);
      // Target: bias toward the top of the model (head) for full-body-ish meshes;
      // for a centered sphere the bias is negligible.
      const target = new THREE.Vector3(
        center.x,
        center.y + size.y * 0.35,
        center.z,
      );
      // Distance to fit the head extent in the vertical FOV, with headroom.
      const headExtent = Math.max(size.y * 0.35, size.x, 0.001);
      const fov = (camera.fov * Math.PI) / 180;
      const dist = (headExtent / Math.tan(fov / 2)) * 1.6 + size.z;
      camera.position.set(target.x, target.y, target.z + dist);
      camera.lookAt(target);
    }
    camera.updateProjectionMatrix();

    ro = new ResizeObserver(() => sizeToContainer());
    ro.observe(container);

    ready = true;
    renderLoop();
    return true;
  }

  // Per-frame sink. Delegates to the pure helper; no allocation of its own.
  function applyMorphs(map) {
    if (!ready || !map) return;
    applyMorphInfluences(morphMeshes, map);
  }

  return {
    init,
    applyMorphs,
    dispose: teardown,
    get ready() { return ready; },
  };
}
