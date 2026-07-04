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
    """Bridges to the compiled C++ ``a2f_stream`` helper, which runs the batch-1
    engine + blendshape solve and emits 68 ARKit coefficients per frame at 60 FPS.

    Per utterance: resample the incoming 24 kHz PCM16 → 16 kHz float, spawn the
    helper, write ``[emotion][audio][end]`` on its stdin (see a2f_stream/main.cpp
    protocol), read framed float vectors from stdout, map to ARKit names, and
    downsample 60 → ``A2F_FPS``.

    NOTE: currently spawns the helper per utterance (engine load ≈ a few seconds
    of startup). A persistent helper (load-once, loop over utterances) is the
    key latency optimisation — tracked in a2f_stream/README.md.
    """

    name = "helper"

    def __init__(self) -> None:
        self.helper_path = os.getenv("A2F_HELPER", "/opt/a2f/a2f_stream")
        self.model_json = os.getenv("A2F_MODEL_JSON", "/opt/a2f/model.json")

    async def stream(self, pcm: bytes, emotion: list[float] | None) -> AsyncGenerator[dict, None]:
        import asyncio
        import struct

        import numpy as np
        import soxr

        from arkit import ARKIT_68

        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if x.size == 0:
            return
        audio16 = soxr.resample(x, SAMPLE_RATE_IN, 16000).astype("<f4")
        emo = emotion if emotion else [0.0] * 10

        proc = await asyncio.create_subprocess_exec(
            self.helper_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "A2F_MODEL_JSON": self.model_json},
        )
        assert proc.stdin and proc.stdout
        proc.stdin.write(struct.pack("<I", len(emo)) + struct.pack(f"<{len(emo)}f", *emo))
        proc.stdin.write(struct.pack("<I", audio16.size) + audio16.tobytes())
        proc.stdin.write(struct.pack("<I", 0))
        await proc.stdin.drain()
        proc.stdin.close()

        stride = max(1, 60 // FPS)  # helper is 60 FPS; emit every `stride`th
        src_i = out_i = 0
        try:
            while True:
                hdr = await proc.stdout.readexactly(4)
                (n,) = struct.unpack("<I", hdr)
                if n == 0:
                    break
                data = await proc.stdout.readexactly(n * 4)
                if src_i % stride == 0:
                    vals = struct.unpack(f"<{n}f", data)
                    yield {"frame": out_i, "t": round(out_i / FPS, 4), "arkit": dict(zip(ARKIT_68, vals))}
                    out_i += 1
                src_i += 1
        except asyncio.IncompleteReadError:
            err = (await proc.stderr.read()).decode() if proc.stderr else ""
            raise RuntimeError(f"a2f_stream ended early: {err.strip()[:300]}")
        finally:
            await proc.wait()


def make_backend():
    backend = os.getenv("A2F_BACKEND", "mock").lower()
    return HelperBackend() if backend == "helper" else MockBackend()
