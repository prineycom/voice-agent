"""A2F inference backend — turns a PCM utterance (+ emotion) into ARKit
blendshape frames at ~30/60 FPS.

Two backends, selected by ``A2F_BACKEND``:

* ``helper`` (production): drives the compiled C++ ``a2f_stream`` helper built
  from the Audio2Face-3D-SDK against the batch-1 TensorRT engine (see
  ``a2f_stream/`` and ``build_engine.sh``). The helper reads PCM frames + an
  emotion vector on stdin and writes blendshape frames on stdout; it runs A2E
  (Audio2Emotion) on the audio as the baseline emotion source, the stdin vector
  acting as an additive boost (#40). VRAM ≈ 1.65 GiB with A2E (the A2E TRT
  engine is ≈ 1.25 GiB of it), coexists with STT+TTS. The helper is a **single
  long-lived process**: it is spawned lazily on the first utterance and kept
  alive across utterances (the engines load once), so per-utterance latency ≈
  inference. The shared stdin/stdout pipe is guarded by a single-flight lock; a
  crash sets the process back to ``None`` so the next call respawns lazily —
  see a2f_stream/README.

* ``mock`` (default / CI / no-GPU): a dependency-free synthetic generator that
  emits well-formed blendshape frames (a gentle idle + audio-envelope-driven
  jaw/brow) so the WS contract, the agent wiring, and the frontend can be built
  and tested before the GPU helper lands.

Both backends expose the same async generator: ``stream(pcm, emotion) -> frames``
where each frame is ``{"frame": i, "t": seconds, "arkit": {name: value}}``.
"""

from __future__ import annotations

import asyncio
import math
import os
import struct
from typing import AsyncGenerator, Iterable

from arkit import ARKIT_52

SAMPLE_RATE_IN = int(os.getenv("A2F_INPUT_RATE", "24000"))  # TTS PCM16 mono
FPS = int(os.getenv("A2F_FPS", "30"))

# I/O timeouts for the persistent helper (seconds). A hung — not crashed —
# helper (GPU stall, deadlocked CUDA/TensorRT) would otherwise wedge the lock
# forever, so every await in the critical section is bounded.
HELPER_TIMEOUT = float(os.getenv("A2F_HELPER_TIMEOUT", "5"))  # per-utterance I/O
# The FIRST call also pays the one-time engine load (~1.5 s) on top of spawn, so
# it gets a wider budget.
HELPER_FIRST_TIMEOUT = float(os.getenv("A2F_HELPER_FIRST_TIMEOUT", "30"))
HELPER_KILL_TIMEOUT = float(os.getenv("A2F_HELPER_KILL_TIMEOUT", "5"))  # kill → reap


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

    async def close(self) -> None:
        """No persistent resource to release — present so the server can call
        ``close()`` uniformly across backends."""
        return


class HelperBackend:
    """Bridges to the compiled C++ ``a2f_stream`` helper, which runs the batch-1
    engine + blendshape solve and emits 68 ARKit coefficients per frame at 60 FPS.

    The helper is a **single persistent process**: it is spawned lazily on the
    first utterance (``_ensure_proc``) and kept alive across utterances so the
    engine loads once and per-utterance latency ≈ inference. Its stdin/stdout is
    one shared pipe, so the whole pipe-touching critical section runs under a
    single-flight lock (``self._lock``) — bytes from concurrent/serial ``stream``
    calls must not interleave. If the helper crashes (``IncompleteReadError`` or a
    broken pipe) we drain stderr, kill it, and set ``self._proc = None`` so the
    next call respawns lazily.

    Per utterance (inside the lock): resample the incoming 24 kHz PCM16 → 16 kHz
    float, write ``[emotion][audio][end]`` on the helper's stdin (see
    a2f_stream/main.cpp protocol) WITHOUT closing stdin (the process persists),
    read framed float vectors from stdout until the done marker, map to ARKit
    names, and downsample 60 → ``A2F_FPS``. Frames are buffered inside the lock
    and yielded after it is released, so a slow WS consumer can't stall the
    shared pipe.
    """

    name = "helper"

    def __init__(self) -> None:
        self.helper_path = os.getenv("A2F_HELPER", "/opt/a2f/a2f_stream")
        self.model_json = os.getenv("A2F_MODEL_JSON", "/opt/a2f/model.json")
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    async def _ensure_proc(self) -> asyncio.subprocess.Process:
        """Spawn the persistent helper if it isn't running (first call or after a
        crash). Must be called under ``self._lock``."""
        if self._proc is None or self._proc.returncode is not None:
            self._proc = await asyncio.create_subprocess_exec(
                self.helper_path,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "A2F_MODEL_JSON": self.model_json},
            )
        return self._proc

    async def _terminate(self, proc: asyncio.subprocess.Process) -> None:
        """Force-kill ``proc`` and reap it (bounded), tolerating an already-exited
        child. Used on every failure exit so no orphan GPU process is left."""
        try:
            proc.kill()  # may already be reaped after a self-exit crash
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=HELPER_KILL_TIMEOUT)
        except asyncio.TimeoutError:
            pass

    async def _utterance_io(
        self, proc: asyncio.subprocess.Process, emo: list[float], audio16, stride: int
    ) -> list[dict]:
        """Write one utterance and read its framed blendshapes until the done
        marker. Runs entirely inside ``self._lock`` (via ``stream``). No cleanup
        here — the caller kills + clears the process on ANY failure exit."""
        from arkit import ARKIT_68

        assert proc.stdin and proc.stdout
        proc.stdin.write(struct.pack("<I", len(emo)) + struct.pack(f"<{len(emo)}f", *emo))
        proc.stdin.write(struct.pack("<I", audio16.size) + audio16.tobytes())
        proc.stdin.write(struct.pack("<I", 0))
        await proc.stdin.drain()

        frames: list[dict] = []
        src_i = out_i = 0
        while True:
            hdr = await proc.stdout.readexactly(4)
            (n,) = struct.unpack("<I", hdr)
            if n == 0:
                break
            data = await proc.stdout.readexactly(n * 4)
            if src_i % stride == 0:
                vals = struct.unpack(f"<{n}f", data)
                frames.append(
                    {"frame": out_i, "t": round(out_i / FPS, 4), "arkit": dict(zip(ARKIT_68, vals))}
                )
                out_i += 1
            src_i += 1
        return frames

    async def stream(self, pcm: bytes, emotion: list[float] | None) -> AsyncGenerator[dict, None]:
        import numpy as np
        import soxr

        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if x.size == 0:
            return
        audio16 = soxr.resample(x, SAMPLE_RATE_IN, 16000).astype("<f4")
        emo = emotion if emotion else [0.0] * 10

        stride = max(1, 60 // FPS)  # helper is 60 FPS; emit every `stride`th
        frames: list[dict] = []
        async with self._lock:
            existing = self._proc
            proc = await self._ensure_proc()
            # The first call after a (re)spawn also pays the one-time engine load.
            timeout = HELPER_FIRST_TIMEOUT if proc is not existing else HELPER_TIMEOUT
            try:
                frames = await asyncio.wait_for(
                    self._utterance_io(proc, emo, audio16, stride), timeout=timeout
                )
            except (
                asyncio.IncompleteReadError,
                BrokenPipeError,
                ConnectionResetError,
                asyncio.TimeoutError,
            ) as exc:
                # Helper crashed (pipe) or hung (timeout) → drain stderr (bounded,
                # it may be hung too), kill, clear so the next call respawns clean.
                err = ""
                if proc.stderr is not None:
                    try:
                        err = (await asyncio.wait_for(proc.stderr.read(), timeout=1.0)).decode()
                    except Exception:  # noqa: BLE001
                        err = ""
                await self._terminate(proc)
                self._proc = None
                reason = "timed out" if isinstance(exc, asyncio.TimeoutError) else "ended early"
                raise RuntimeError(f"a2f_stream {reason}: {err.strip()[:300]}")
            except BaseException:
                # Cancellation (WS disconnect / wait_for timeout / task supersession)
                # or any other error may leave stdin half-written or stdout
                # half-drained → the wire is desynced. Kill + clear so the next
                # call respawns, but let the original exception propagate (never
                # swallow CancelledError into RuntimeError).
                await self._terminate(proc)
                self._proc = None
                raise

        for frame in frames:  # yield outside the lock — a slow consumer must not stall the pipe
            yield frame

    async def close(self) -> None:
        """Cleanly stop the persistent helper on server shutdown: close its stdin
        (EOF → the helper's documented clean exit), reap it (bounded), then
        force-kill if it lingers. Prevents an orphan GPU process holding VRAM + a
        TensorRT context after an ungraceful stop."""
        async with self._lock:
            proc, self._proc = self._proc, None
            if proc is None or proc.returncode is not None:
                return
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=HELPER_KILL_TIMEOUT)
            except asyncio.TimeoutError:
                await self._terminate(proc)


def make_backend():
    backend = os.getenv("A2F_BACKEND", "mock").lower()
    return HelperBackend() if backend == "helper" else MockBackend()
