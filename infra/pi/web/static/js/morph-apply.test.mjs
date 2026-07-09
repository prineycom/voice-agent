// Zero-dependency Node ESM test for morph-apply.js: applyMorphInfluences maps a
// { morphName: value } dict onto an array of three.js-like meshes, driving EVERY
// mesh that carries a given morph (RPM head+teeth both hold jawOpen) and leaving
// meshes without the key untouched. Fake meshes are plain objects.
// Run: node infra/pi/web/static/js/morph-apply.test.mjs
import assert from 'node:assert/strict';
import { applyMorphInfluences } from './morph-apply.js';

let assertions = 0;
function eq(actual, expected, msg) {
  assert.equal(actual, expected, msg);
  assertions++;
}
function ok(value, msg) {
  assert.ok(value, msg);
  assertions++;
}

// Build a fake mesh: dictionary maps names -> index, influences zero-filled.
function mesh(dict) {
  const size = Object.keys(dict).length;
  return { morphTargetDictionary: dict, morphTargetInfluences: new Array(size).fill(0) };
}

// (1) head + teeth both carry jawOpen at different indices -> both driven.
{
  const head = mesh({ jawOpen: 3, mouthSmileLeft: 1 });
  const teeth = mesh({ jawOpen: 0 });
  applyMorphInfluences([head, teeth], { jawOpen: 0.8 });
  eq(head.morphTargetInfluences[3], 0.8, '1: head jawOpen driven');
  eq(teeth.morphTargetInfluences[0], 0.8, '1: teeth jawOpen driven (same value, other index)');
  eq(head.morphTargetInfluences[1], 0, '1: unrelated head morph untouched');
}

// (2) an eye mesh with no mouth key is left untouched by that key.
{
  const eye = mesh({ eyeBlinkLeft: 0, eyeLookInLeft: 1 });
  const before = eye.morphTargetInfluences.slice();
  applyMorphInfluences([eye], { jawOpen: 1.0 });
  ok(eye.morphTargetInfluences.every((v, i) => v === before[i]),
    '2: eye mesh untouched by absent mouth key');
  // ...but a key it DOES have still gets applied in the same call.
  applyMorphInfluences([eye], { jawOpen: 1.0, eyeBlinkLeft: 0.5 });
  eq(eye.morphTargetInfluences[0], 0.5, '2: present eye key applied; absent jawOpen ignored');
}

// (3) unknown morph name absent from all dicts -> no-op, no throw.
{
  const head = mesh({ jawOpen: 0 });
  assert.doesNotThrow(() => applyMorphInfluences([head], { totallyUnknown: 0.9 }));
  eq(head.morphTargetInfluences[0], 0, '3: unknown morph does not touch anything');
  assertions++; // for the doesNotThrow above
}

// (4) meshes missing dictionary/influences are skipped without throwing.
{
  const noDict = { morphTargetInfluences: [0, 0] };
  const noInfluences = { morphTargetDictionary: { jawOpen: 0 } };
  const good = mesh({ jawOpen: 1 });
  assert.doesNotThrow(() =>
    applyMorphInfluences([noDict, noInfluences, null, undefined, good], { jawOpen: 0.7 }));
  assertions++;
  eq(good.morphTargetInfluences[1], 0.7, '4: valid mesh still driven when others are malformed');
  eq(noDict.morphTargetInfluences[0], 0, '4: dict-less mesh left untouched');
}

console.log(`morph-apply.js: all ${assertions} assertions passed`);
process.exit(0);
