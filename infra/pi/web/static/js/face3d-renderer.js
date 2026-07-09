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
    // updateStyle=true so the canvas CSS box matches the container; false would
    // leave the canvas at its raw buffer size (container×devicePixelRatio) and
    // overflow the stage, covering the header controls.
    renderer.setSize(w, h, true);
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
