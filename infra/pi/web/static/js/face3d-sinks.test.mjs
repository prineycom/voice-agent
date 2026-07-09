// Zero-dependency Node ESM test for face3d-sinks.js: the guarded facial + jaw
// sink pair driven by blendshapes.js. Injects a recording/throwing fake renderer
// (no WebGL) and asserts the DD-3 ownership split (facial writes all morphs but
// jawOpen; mouth writes ONLY jawOpen) plus the retro GUARD — a throw in
// facial.apply must NOT stop the following mouth.ingestA2FFrame.
// Run: node infra/pi/web/static/js/face3d-sinks.test.mjs
import assert from 'node:assert/strict';
import { createFaceSinks } from './face3d-sinks.js';

let assertions = 0;
function eq(actual, expected, msg) { assert.equal(actual, expected, msg); assertions++; }
function deep(actual, expected, msg) { assert.deepEqual(actual, expected, msg); assertions++; }
function ok(value, msg) { assert.ok(value, msg); assertions++; }

// Recording renderer: every applyMorphs(map) lands (shallow-copied) in `maps`.
function recordingRenderer() {
  const maps = [];
  return { maps, applyMorphs: (m) => maps.push({ ...m }) };
}

// (a) Ownership split: facial excludes jawOpen and includes facial morphs; the
// mouth writes ONLY jawOpen. No morph key is written by BOTH in one frame.
{
  const r = recordingRenderer();
  const { facial, mouth } = createFaceSinks(r);
  const frame = { arkit: { JawOpen: 0.7, MouthSmileLeft: 0.3, EyeBlinkLeft: 0.5 } };

  facial.apply(frame.arkit);
  const facialMap = r.maps[0];
  ok(!('jawOpen' in facialMap), 'a: facial map EXCLUDES jawOpen');
  eq(facialMap.mouthSmileLeft, 0.3, 'a: facial map INCLUDES mouthSmileLeft');
  eq(facialMap.eyeBlinkLeft, 0.5, 'a: facial map INCLUDES eyeBlinkLeft');

  mouth.ingestA2FFrame(frame);
  const mouthMap = r.maps[1];
  deep(mouthMap, { jawOpen: 0.7 }, 'a: mouth map is exactly {jawOpen:0.7}');

  // No overlap: no key appears in both facial and mouth map for this frame.
  const overlap = Object.keys(facialMap).filter((k) => k in mouthMap);
  deep(overlap, [], 'a: no morph key written by BOTH facial and mouth in one frame');
}

// (b) GUARD (the retro bug): a renderer that THROWS inside facial.apply must not
// propagate. Simulate blendshapes.js' apply(): facial.apply(f.arkit) then, with
// NO try/catch around it, mouth.ingestA2FFrame(f). The mouth's jaw write must
// still fire with the correct value.
{
  let facialCalls = 0;
  let mouthCall = null;
  const throwingRenderer = {
    applyMorphs(m) {
      if ('jawOpen' in m && Object.keys(m).length === 1) { mouthCall = m; return; }
      facialCalls++;
      throw new Error('boom in face renderer');
    },
  };
  let logged = 0;
  const { facial, mouth } = createFaceSinks(throwingRenderer, { log: () => { logged++; } });
  const frame = { arkit: { JawOpen: 0.42, MouthSmileLeft: 0.9 } };

  // Mirror blendshapes.js lines 45-46 with no guard between them.
  assert.doesNotThrow(() => { facial.apply(frame.arkit); }, 'b: facial.apply swallows the throw');
  assertions++;
  mouth.ingestA2FFrame(frame);

  eq(facialCalls, 1, 'b: facial renderer was invoked (and threw)');
  ok(mouthCall !== null, 'b: mouth applyMorphs STILL fired after the facial throw');
  eq(mouthCall.jawOpen, 0.42, 'b: mouth wrote the correct jaw value despite the throw');
  ok(logged >= 1, 'b: the swallowed throw was logged');
}

// (c) release() zeroes the facial morphs — recorded map has all-zero values and
// covers the keys facial previously wrote (mouthSmileLeft etc), never jawOpen.
{
  const r = recordingRenderer();
  const { facial } = createFaceSinks(r);
  facial.apply({ MouthSmileLeft: 0.3, EyeBlinkLeft: 0.5, JawOpen: 0.8 });
  facial.release();
  const rest = r.maps[r.maps.length - 1];
  ok('mouthSmileLeft' in rest, 'c: release zeroes previously-written morphs');
  ok(!('jawOpen' in rest), 'c: release never writes jawOpen (mouth-owned)');
  const allZero = Object.values(rest).every((v) => v === 0);
  ok(allZero, 'c: every value in the neutral rest map is 0');
  ok(Object.keys(rest).length > 0, 'c: neutral map is non-empty after an apply');
}

// (d) clamp: JawOpen out of range and undefined fold to [0,1].
{
  const r = recordingRenderer();
  const { mouth } = createFaceSinks(r);
  mouth.ingestA2FFrame({ arkit: { JawOpen: 1.5 } });
  eq(r.maps[0].jawOpen, 1, 'd: 1.5 clamps to 1');
  mouth.ingestA2FFrame({ arkit: { JawOpen: -0.2 } });
  eq(r.maps[1].jawOpen, 0, 'd: -0.2 clamps to 0');
  mouth.ingestA2FFrame({ arkit: {} });
  eq(r.maps[2].jawOpen, 0, 'd: undefined JawOpen folds to 0');
}

console.log(`face3d-sinks.js: all ${assertions} assertions passed`);
process.exit(0);
