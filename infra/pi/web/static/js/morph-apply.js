// Pure, GL-free helper: applies a morph-influence map across an array of
// three.js-like meshes. No three.js / WebGL import — meshes are duck-typed by
// their { morphTargetDictionary, morphTargetInfluences } shape.
//
// RPM multi-mesh reality: a single blendshape (e.g. jawOpen) lives on more than
// one mesh (head AND teeth), and eye morphs live on the eye meshes. So each
// named morph must be pushed to EVERY mesh whose dictionary carries it.
// Gathering the mesh array is the renderer's job; this module only applies.

/**
 * Apply a { morphName: value } map across meshes.
 *
 * For each entry, every mesh whose morphTargetDictionary contains that name
 * gets mesh.morphTargetInfluences[dict[name]] = value. Names absent from a
 * mesh's dictionary are silently ignored; meshes missing dictionary or
 * influences are skipped without throwing.
 *
 * @param {Array<{morphTargetDictionary?: Object, morphTargetInfluences?: number[]}>} meshes
 * @param {Object<string, number>} morphMap
 */
export function applyMorphInfluences(meshes, morphMap) {
  if (!Array.isArray(meshes) || !morphMap) return;
  const entries = Object.entries(morphMap);
  for (const mesh of meshes) {
    if (!mesh) continue;
    const dict = mesh.morphTargetDictionary;
    const influences = mesh.morphTargetInfluences;
    if (!dict || !influences) continue;
    for (const [name, value] of entries) {
      const index = dict[name];
      if (index === undefined) continue; // key not on this mesh -> ignore
      influences[index] = value;
    }
  }
}
