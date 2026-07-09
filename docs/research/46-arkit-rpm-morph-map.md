# ARKit-52 → Ready Player Me morph-name mapping

Date: 2026-07-09. Fulfils the issue #46 acceptance criterion: "ARKit-52 → RPM
morph-name mapping verified and documented; unmapped/renamed targets listed."
See ADR-0017 (3D-face ARKit pipeline) and issue #46.

## What this maps and why

The A2F service emits per-frame ARKit blendshape coefficients (0.0–1.0) under the
canonical **PascalCase** ARKit-52 names (`MouthSmileLeft`, `JawOpen`, …) — see
`infra/desktop/a2f/arkit.py` (`ARKIT_52`). Ready Player Me (RPM) avatars expose
the exact same ARKit shapes as glTF morph targets, but under **camelCase** names
(`mouthSmileLeft`, `jawOpen`, …), which is also the three.js convention.

Because the two vocabularies are the *same 52 shapes* differing only in the case
of the first character, the mapping is a **mechanical, 1:1, first-char-lowercase**
transform:

```
RPM_name = A2F_name[0].toLowerCase() + A2F_name.slice(1)
```

The bridge is implemented as pure functions in
`infra/pi/web/static/js/arkit-rpm-map.js` (`arkitNameToRpm`,
`arkitToRpmMorphs`) — no GL, no three.js, no DOM — so it is directly
unit-testable. That module is the source of truth for this document.

## ARKit-52 → RPM morph target (52 mapped rows)

| A2F name (PascalCase) | RPM morph target (camelCase) |
| --- | --- |
| EyeBlinkLeft | eyeBlinkLeft |
| EyeLookDownLeft | eyeLookDownLeft |
| EyeLookInLeft | eyeLookInLeft |
| EyeLookOutLeft | eyeLookOutLeft |
| EyeLookUpLeft | eyeLookUpLeft |
| EyeSquintLeft | eyeSquintLeft |
| EyeWideLeft | eyeWideLeft |
| EyeBlinkRight | eyeBlinkRight |
| EyeLookDownRight | eyeLookDownRight |
| EyeLookInRight | eyeLookInRight |
| EyeLookOutRight | eyeLookOutRight |
| EyeLookUpRight | eyeLookUpRight |
| EyeSquintRight | eyeSquintRight |
| EyeWideRight | eyeWideRight |
| JawForward | jawForward |
| JawLeft | jawLeft |
| JawRight | jawRight |
| JawOpen | jawOpen *(written by the mouth sink, excluded from facial.apply via excludeJaw — see DD-3 note below)* |
| MouthClose | mouthClose |
| MouthFunnel | mouthFunnel |
| MouthPucker | mouthPucker |
| MouthLeft | mouthLeft |
| MouthRight | mouthRight |
| MouthSmileLeft | mouthSmileLeft |
| MouthSmileRight | mouthSmileRight |
| MouthFrownLeft | mouthFrownLeft |
| MouthFrownRight | mouthFrownRight |
| MouthDimpleLeft | mouthDimpleLeft |
| MouthDimpleRight | mouthDimpleRight |
| MouthStretchLeft | mouthStretchLeft |
| MouthStretchRight | mouthStretchRight |
| MouthRollLower | mouthRollLower |
| MouthRollUpper | mouthRollUpper |
| MouthShrugLower | mouthShrugLower |
| MouthShrugUpper | mouthShrugUpper |
| MouthPressLeft | mouthPressLeft |
| MouthPressRight | mouthPressRight |
| MouthLowerDownLeft | mouthLowerDownLeft |
| MouthLowerDownRight | mouthLowerDownRight |
| MouthUpperUpLeft | mouthUpperUpLeft |
| MouthUpperUpRight | mouthUpperUpRight |
| BrowDownLeft | browDownLeft |
| BrowDownRight | browDownRight |
| BrowInnerUp | browInnerUp |
| BrowOuterUpLeft | browOuterUpLeft |
| BrowOuterUpRight | browOuterUpRight |
| CheekPuff | cheekPuff |
| CheekSquintLeft | cheekSquintLeft |
| CheekSquintRight | cheekSquintRight |
| NoseSneerLeft | noseSneerLeft |
| NoseSneerRight | noseSneerRight |
| TongueOut | tongueOut |

`TongueOut` **is** part of the standard ARKit-52 and **is** mapped (to
`tongueOut`); it is distinct from the 16 extended `Tongue*` shapes listed below.

## Jaw ownership note (DD-3)

`JawOpen` → `jawOpen` appears in the mapping table above, but the **facial sink
deliberately does not write it**. The jaw is owned by the **mouth sink**:
`mouth.ingestA2FFrame` writes `jawOpen = clamp01(JawOpen)` from the raw A2F
coefficient. To avoid two writers fighting over the same morph target, the facial
path calls `arkitToRpmMorphs(arkit, { excludeJaw: true })`, which drops `JawOpen`
from its output (see `arkit-rpm-map.js` lines 60–70). Net effect: `jawOpen` is
driven solely by the mouth sink; `facial.apply` never touches it.

## Unmapped / renamed targets (16 extended Tongue* shapes)

The A2F James model / SDK blendshape solve emits **68** coefficients: the 52
standard ARKit shapes above plus **16 extended `Tongue*` shapes** (`TONGUE_16` in
both `arkit.py` and `arkit-rpm-map.js`). RPM avatars have **no morph target** for
these, so they are intentionally unmapped (`RPM_UNMAPPED` in `arkit-rpm-map.js`)
and dropped from the output. Listed below in their camelCase form for reference:

| Unmapped (camelCase) | Reason |
| --- | --- |
| tongueTipUp | No RPM morph target — extended A2F-James tongue shape, skipped |
| tongueTipDown | No RPM morph target — extended A2F-James tongue shape, skipped |
| tongueTipLeft | No RPM morph target — extended A2F-James tongue shape, skipped |
| tongueTipRight | No RPM morph target — extended A2F-James tongue shape, skipped |
| tongueRollUp | No RPM morph target — extended A2F-James tongue shape, skipped |
| tongueRollDown | No RPM morph target — extended A2F-James tongue shape, skipped |
| tongueRollLeft | No RPM morph target — extended A2F-James tongue shape, skipped |
| tongueRollRight | No RPM morph target — extended A2F-James tongue shape, skipped |
| tongueUp | No RPM morph target — extended A2F-James tongue shape, skipped |
| tongueDown | No RPM morph target — extended A2F-James tongue shape, skipped |
| tongueLeft | No RPM morph target — extended A2F-James tongue shape, skipped |
| tongueRight | No RPM morph target — extended A2F-James tongue shape, skipped |
| tongueIn | No RPM morph target — extended A2F-James tongue shape, skipped |
| tongueStretch | No RPM morph target — extended A2F-James tongue shape, skipped |
| tongueWide | No RPM morph target — extended A2F-James tongue shape, skipped |
| tongueNarrow | No RPM morph target — extended A2F-James tongue shape, skipped |

Any other unknown/extra keys in an A2F frame are also skipped by
`arkitToRpmMorphs` (only the recognized ARKit-52 names are mapped).

## Verification

The mapping is unit-tested. Running

```
node infra/pi/web/static/js/arkit-rpm-map.test.mjs
```

is green (`arkit-rpm-map.js: all 20 assertions passed`), confirming the 1:1
first-char-lowercase transform, the `excludeJaw` jaw-ownership behaviour, and that
the 16 extended `Tongue*` shapes are dropped.
