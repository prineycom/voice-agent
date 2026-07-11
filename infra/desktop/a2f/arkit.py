"""ARKit blendshape vocabulary emitted by the A2F service.

The Audio2Face model outputs these named ARKit face coefficients (0.0–1.0). The
order matches the SDK / NIM `animation_frames.csv` header. The browser maps these
to Live2D parameters via `arkitToLive2D()` (see Epic 8 / research doc §9); the
mouth-*open* coefficient (`JawOpen`) now drives client mouth opening via the
pluggable `A2FLipSync` provider (A2F primary, volume analyser as fallback/toggle)
per ADR-0013.
"""

# 52 standard ARKit blendshapes, in canonical order. The A2F James model also
# emits extended Tongue* shapes; those are appended and pass through when present.
ARKIT_52 = [
    "EyeBlinkLeft", "EyeLookDownLeft", "EyeLookInLeft", "EyeLookOutLeft", "EyeLookUpLeft",
    "EyeSquintLeft", "EyeWideLeft",
    "EyeBlinkRight", "EyeLookDownRight", "EyeLookInRight", "EyeLookOutRight", "EyeLookUpRight",
    "EyeSquintRight", "EyeWideRight",
    "JawForward", "JawLeft", "JawRight", "JawOpen",
    "MouthClose", "MouthFunnel", "MouthPucker", "MouthLeft", "MouthRight",
    "MouthSmileLeft", "MouthSmileRight", "MouthFrownLeft", "MouthFrownRight",
    "MouthDimpleLeft", "MouthDimpleRight", "MouthStretchLeft", "MouthStretchRight",
    "MouthRollLower", "MouthRollUpper", "MouthShrugLower", "MouthShrugUpper",
    "MouthPressLeft", "MouthPressRight", "MouthLowerDownLeft", "MouthLowerDownRight",
    "MouthUpperUpLeft", "MouthUpperUpRight",
    "BrowDownLeft", "BrowDownRight", "BrowInnerUp", "BrowOuterUpLeft", "BrowOuterUpRight",
    "CheekPuff", "CheekSquintLeft", "CheekSquintRight",
    "NoseSneerLeft", "NoseSneerRight",
    "TongueOut",
]

# The A2F James model / SDK blendshape solve emits 68 coefficients: the 52 above
# (skin) + 16 extended tongue shapes, in this order (matches the NIM's
# animation_frames.csv header). a2f_stream writes these 68 floats per frame.
TONGUE_16 = [
    "TongueTipUp", "TongueTipDown", "TongueTipLeft", "TongueTipRight",
    "TongueRollUp", "TongueRollDown", "TongueRollLeft", "TongueRollRight",
    "TongueUp", "TongueDown", "TongueLeft", "TongueRight",
    "TongueIn", "TongueStretch", "TongueWide", "TongueNarrow",
]
ARKIT_68 = ARKIT_52 + TONGUE_16

# Emotion enum shared with SOUL.md / the agent (decision #5; expanded 5 → 10 in
# ADR-0020). Mapped to the A2F emotion input vector by `emotion.py`.
EMOTIONS = [
    "neutral", "happy", "sad", "excited", "calm",
    "serious", "surprised", "angry", "tender", "thinking",
]
