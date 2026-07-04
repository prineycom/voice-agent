"""A2F inference backend — turns a PCM utterance (+ emotion) into ARKit
blendshape frames at ~30/60 FPS.

Two backends, selected by ``A2F_BACKEND``:

* ``helper`` (production): drives the compiled C++ ``a2f_stream`` helper built
  from the Audio2Face-3D-SDK against the batch-1 TensorRT engine (see
  ``a2f_stream/`` and ``build_engine.sh``). The helper reads PCM frames + an
  emotion vector on stdin and writes blendshape frames on stdout. VRAM ≈ 0.3 GB
  (spike-measured), coexists with STT+TTS. **The helper's inference wiring is
  still WIP** (SDK InteractiveExecutor + BlendshapeSolve) — see a2f_stream/README.

* ``mock`` (default / CI / no-GPU): a dependency-free synthetic generator that
  emits well-formed blendshape frames (a gentle idle + audio-envelope-driven
  jaw/brow) so the WS contract, the agent wiring, and the frontend can be built
  and tested before the GPU helper lands.

Both backends expose the same async generator: ``stream(pcm, emotion) -> frames``
where each frame is ``{"frame": i, "t": seconds, "arkit": {name: value}}``.
"""

from __future__ import annotations

import math
import os
import struct
from typing import AsyncGenerator, Iterable

from arkit import ARKIT_52

SAMPLE_RATE_IN = int(os.getenv("A2F_INPUT_RATE", "24000"))  # TTS PCM16 mono
FPS = int(os.getenv("A2F_FPS", "30"))


def _pcm16_to_float(pcm: bytes) -> list[float]:
    n = len(pcm) // 2
    if n == 0:
        return []
    return [s / 32768.0 for s in struct.unpack(f"<{n}h", pcm[: n * 2])]


class MockBackend:
    """Synthetic but well-formed blendshapes. No GPU, no model — for contract
    and integration testing. Jaw follows the audio envelope; eyes blink on a
    timer; a small emotion-driven brow/mouth-form bias is applied."""

    name = "mock"

    async def stream(self, pcm: bytes, emotion: list[float] | None) -> AsyncGenerator[dict, None]:
        samples = _pcm16_to_float(pcm)
        if not samples:
            return
        spf = max(1, SAMPLE_RATE_IN // FPS)  # samples per frame
        n_frames = max(1, len(samples) // spf)
        happy = (emotion or [0.0] * 5)[1] if emotion else 0.0
        sad = (emotion or [0.0] * 5)[2] if emotion else 0.0
        for i in range(n_frames):
            window = samples[i * spf : (i + 1) * spf]
            rms = math.sqrt(sum(v * v for v in window) / len(window)) if window else 0.0
            jaw = min(1.0, rms * 6.0)
            blink = 1.0 if (i % (FPS * 3) < 2) else 0.0  # ~1 blink / 3 s
            frame = {name: 0.0 for name in ARKIT_52}
            frame["JawOpen"] = jaw
            frame["MouthFunnel"] = jaw * 0.3
            frame["EyeBlinkLeft"] = blink
            frame["EyeBlinkRight"] = blink
            frame["MouthSmileLeft"] = frame["MouthSmileRight"] = 0.4 * happy
            frame["MouthFrownLeft"] = frame["MouthFrownRight"] = 0.4 * sad
            frame["BrowInnerUp"] = 0.3 * sad
            yield {"frame": i, "t": round(i / FPS, 4), "arkit": frame}


class HelperBackend:
    """Bridges to the compiled C++ ``a2f_stream`` helper (WIP). Kept import-safe
    so the module loads without the helper present; ``stream`` raises until the
    helper binary + engine exist."""

    name = "helper"

    def __init__(self) -> None:
        self.helper_path = os.getenv("A2F_HELPER", "/opt/a2f/a2f_stream")
        self.engine_path = os.getenv("A2F_ENGINE", "/opt/a2f/network.trt")

    async def stream(self, pcm: bytes, emotion: list[float] | None) -> AsyncGenerator[dict, None]:
        # TODO(Phase 1): spawn the helper (asyncio subprocess), write the emotion
        # vector + resampled-to-16k float PCM to stdin, parse blendshape frames
        # from stdout. Blocked on the helper's SDK inference wiring — see
        # a2f_stream/README.md. Until then, fail loudly rather than silently mock.
        raise NotImplementedError(
            "A2F helper backend not wired yet; run with A2F_BACKEND=mock or build a2f_stream"
        )
        yield  # pragma: no cover — makes this an async generator


def make_backend():
    backend = os.getenv("A2F_BACKEND", "mock").lower()
    return HelperBackend() if backend == "helper" else MockBackend()
