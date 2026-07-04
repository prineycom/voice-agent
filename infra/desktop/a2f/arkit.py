"""ARKit blendshape vocabulary emitted by the A2F service.

The Audio2Face model outputs these named ARKit face coefficients (0.0–1.0). The
order matches the SDK / NIM `animation_frames.csv` header. The browser maps these
to Live2D parameters via `arkitToLive2D()` (see Epic 8 / research doc §9); the
mouth-*open* coefficient (`JawOpen`) is intentionally NOT used on the client — the
mouth amplitude stays on the volume analyser (ADR-0008, hybrid decision #1).
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

# Emotion enum shared with SOUL.md / the agent (decision #5). Mapped to the A2F
# emotion input vector by `emotion.py`.
EMOTIONS = ["neutral", "happy", "sad", "surprised", "thinking"]
