// Zero-dependency Node ESM test for the pure ARKit→RPM name mapper in
// arkit-rpm-map.js. Run: node infra/pi/web/static/js/arkit-rpm-map.test.mjs
import assert from 'node:assert/strict';
import { arkitNameToRpm, arkitToRpmMorphs, RPM_UNMAPPED } from './arkit-rpm-map.js';

let assertions = 0;
function eq(actual, expected, msg) {
  assert.deepEqual(actual, expected, msg);
  assertions++;
}
function ok(value, msg) {
  assert.ok(value, msg);
  assertions++;
}

// arkitNameToRpm: first char lowercased, rest untouched.
eq(arkitNameToRpm('MouthSmileLeft'), 'mouthSmileLeft', 'MouthSmileLeft -> mouthSmileLeft');
eq(arkitNameToRpm('JawOpen'), 'jawOpen', 'JawOpen -> jawOpen');
eq(arkitNameToRpm('BrowInnerUp'), 'browInnerUp', 'BrowInnerUp -> browInnerUp');

// arkitToRpmMorphs: maps a sample frame, values pass through unchanged.
const frame = arkitToRpmMorphs({ JawOpen: 0.42, MouthSmileLeft: 0.3, MouthSmileRight: 0.31 });
eq(frame.jawOpen, 0.42, 'sample frame -> jawOpen 0.42');
eq(frame.mouthSmileLeft, 0.3, 'sample frame -> mouthSmileLeft 0.3');
eq(frame.mouthSmileRight, 0.31, 'sample frame -> mouthSmileRight 0.31');

// excludeJaw:true drops JawOpen but keeps other mouth morphs.
const noJaw = arkitToRpmMorphs({ JawOpen: 0.42, MouthSmileLeft: 0.3 }, { excludeJaw: true });
ok(!('jawOpen' in noJaw), 'excludeJaw -> jawOpen dropped');
eq(noJaw.mouthSmileLeft, 0.3, 'excludeJaw -> mouthSmileLeft kept');

// Default (no opts) keeps JawOpen.
ok('jawOpen' in arkitToRpmMorphs({ JawOpen: 1 }), 'default -> jawOpen kept');

// Extended Tongue* keys are excluded (no RPM morph target).
const withTongue = arkitToRpmMorphs({ TongueTipUp: 0.9, MouthClose: 0.1, TongueOut: 0.5 });
ok(!('tongueTipUp' in withTongue), 'extended TongueTipUp excluded');
eq(withTongue.mouthClose, 0.1, 'MouthClose still mapped alongside tongue keys');
// TongueOut is part of ARKIT_52 (a real RPM target), so it *is* mapped.
eq(withTongue.tongueOut, 0.5, 'TongueOut (ARKit-52) -> tongueOut mapped');

// RPM_UNMAPPED lists the 16 extended Tongue* names (camelCase).
eq(RPM_UNMAPPED.length, 16, 'RPM_UNMAPPED has 16 entries');
ok(RPM_UNMAPPED.includes('tongueTipUp'), 'RPM_UNMAPPED includes tongueTipUp');
ok(RPM_UNMAPPED.every(n => n[0] === n[0].toLowerCase()), 'RPM_UNMAPPED entries are camelCase');
ok(!RPM_UNMAPPED.includes('tongueOut'), 'RPM_UNMAPPED excludes tongueOut (a mapped ARKit-52 shape)');

// Unknown/non-ARKit keys don't crash and are skipped.
const messy = arkitToRpmMorphs({ NotAShape: 1, foo: 2, JawOpen: 0.7 });
eq(messy.jawOpen, 0.7, 'unknown keys skipped, JawOpen still mapped');
ok(!('notAShape' in messy) && !('foo' in messy), 'unknown keys not emitted');

// Empty / nullish input is safe.
eq(arkitToRpmMorphs({}), {}, 'empty object -> empty object');
eq(arkitToRpmMorphs(null), {}, 'null -> empty object');

console.log(`arkit-rpm-map.js: all ${assertions} assertions passed`);
process.exit(0);
