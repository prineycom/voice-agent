"""A2F inference backend — turns a PCM utterance (+ emotion) into ARKit
blendshape frames at ~30/60 FPS, STREAMING: frames flow while audio chunks are
still arriving.

Two backends, selected by ``A2F_BACKEND``:

* ``helper`` (production): drives the compiled C++ ``a2f_stream`` helper built
  from the Audio2Face-3D-SDK against the batch-1 TensorRT engine (see
  ``a2f_stream/`` and ``build_engine.sh``). The helper reads PCM chunks + an
  emotion vector on stdin and writes blendshape frames on stdout INTERLEAVED
  with the incoming chunks (streaming executors); it runs A2E (Audio2Emotion)
  on the audio as the baseline emotion source, the stdin vector acting as an
  additive boost (#40). VRAM ≈ 1.65 GiB with A2E (the A2E TRT engine is ≈ 1.25
  GiB of it), coexists with STT+TTS. The helper is a **single long-lived
  process**: it is spawned lazily on the first utterance and kept alive across
  utterances (the engines load once), so per-utterance latency ≈ inference. The
  shared stdin/stdout pipe is guarded by a single-flight lock; a crash sets the
  process back to ``None`` so the next call respawns lazily — see
  a2f_stream/README.

* ``mock`` (default / CI / no-GPU): a dependency-free synthetic generator that
  emits well-formed blendshape frames (a gentle idle + audio-envelope-driven
  jaw/brow) so the WS contract, the agent wiring, and the frontend can be built
  and tested before the GPU helper lands.

Both backends expose the same PRIMARY async generator:
``stream_from(chunks, emotion) -> frames`` where ``chunks`` is an async
iterator of PCM16 @ 24 kHz mono byte chunks and each frame is
``{"frame": i, "t": seconds, "arkit": {name: value}}``. ``stream(pcm, emotion)``
is a thin back-compat wrapper that feeds a pre-buffered utterance as one chunk.
"""

from __future__ import annotations

import asyncio
import math
import os
import struct
from typing import AsyncGenerator, AsyncIterator

from arkit import ARKIT_52

SAMPLE_RATE_IN = int(os.getenv("A2F_INPUT_RATE", "24000"))  # TTS PCM16 mono
SAMPLE_RATE_MODEL = 16000  # the A2F model consumes 16 kHz float mono
FPS = int(os.getenv("A2F_FPS", "30"))

# I/O timeouts for the persistent helper (seconds). A hung — not crashed —
# helper (GPU stall, deadlocked CUDA/TensorRT) would otherwise wedge the lock
# forever, so every await in the critical section is bounded. There is NO
# whole-utterance budget (an utterance now lasts as long as its audio keeps
# arriving): A2F_HELPER_TIMEOUT bounds each individual stdout read (inactivity)
# and each stdin drain instead.
HELPER_TIMEOUT = float(os.getenv("A2F_HELPER_TIMEOUT", "5"))  # per-read / per-drain
# The FIRST stdout read after a (re)spawn also pays the one-time engine load
# (~1.5 s) on top of spawn, so it gets a wider budget.
HELPER_FIRST_TIMEOUT = float(os.getenv("A2F_HELPER_FIRST_TIMEOUT", "30"))
HELPER_KILL_TIMEOUT = float(os.getenv("A2F_HELPER_KILL_TIMEOUT", "5"))  # kill → reap
# When the consumer abandons an utterance mid-stream we abort it on the helper
# and drain its remaining frames to the done marker; bounded so a hung helper
# falls back to kill + respawn.
ABORT_DRAIN_TIMEOUT = 2.0

# Chunk-header position marker telling the helper to abort the current
# utterance (see a2f_stream/main.cpp kAbortMarker): it stops computing, emits
# the normal [u32 0] done marker, and waits for the next utterance.
ABORT_MARKER = 0xFFFFFFFF

_DONE = object()  # queue sentinel: the reader consumed the done marker


class _ChunkSourceError(Exception):
    """Wraps an exception raised by the caller's chunk iterator (e.g. the TTS
    fork dying mid-utterance) so it is distinguishable from wire errors: the
    utterance was aborted on the helper, which STAYS in service."""

    def __init__(self, original: BaseException) -> None:
        super().__init__(str(original))
        self.original = original


class _UtteranceState:
    """Writer progress markers the abort path needs to reason about stdin
    message boundaries. Each flag is set with no await between the flag and the
    (synchronous, whole-message) ``write()`` it describes, so the flags are
    exact once the writer task has been cancelled and awaited."""

    emotion_sent = False  # emotion header entered the stdin transport buffer
    end_sent = False  # [u32 0] end-of-utterance marker entered the buffer
    abort_sent = False  # [u32 ABORT_MARKER] entered the buffer


def _pcm16_to_float(pcm: bytes) -> list[float]:
    n = len(pcm) // 2
    if n == 0:
        return []
    return [s / 32768.0 for s in struct.unpack(f"<{n}h", pcm[: n * 2])]


class _BackendBase:
    """Shared API surface. ``stream_from`` (chunked, streaming) is the primary
    generator each backend implements; ``stream`` is the back-compat wrapper
    that feeds a whole pre-buffered utterance as a single chunk."""

    def stream_from(
        self, chunks: AsyncIterator[bytes], emotion: list[float] | None
    ) -> AsyncGenerator[dict, None]:
        raise NotImplementedError

    async def stream(self, pcm: bytes, emotion: list[float] | None) -> AsyncGenerator[dict, None]:
        async def one_chunk() -> AsyncGenerator[bytes, None]:
            yield pcm

        gen = self.stream_from(one_chunk(), emotion)
        try:
            async for frame in gen:
                yield frame
        finally:
            # Deterministic teardown: if THIS wrapper is aclose()d/cancelled,
            # close the inner generator now (its abort/cleanup logic must not
            # wait for garbage collection).
            await gen.aclose()

    async def close(self) -> None:
        """No persistent resource to release by default — present so the server
        can call ``close()`` uniformly across backends."""
        return


class MockBackend(_BackendBase):
    """Synthetic but well-formed blendshapes. No GPU, no model — for contract
    and integration testing. Jaw follows the audio envelope; eyes blink on a
    timer; a small emotion-driven brow/mouth-form bias is applied. Frames are
    emitted incrementally as chunks arrive (same contract as the helper)."""

    name = "mock"

    async def stream_from(
        self, chunks: AsyncIterator[bytes], emotion: list[float] | None
    ) -> AsyncGenerator[dict, None]:
        spf = max(1, SAMPLE_RATE_IN // FPS)  # samples per frame
        happy = (emotion or [0.0] * 5)[1] if emotion else 0.0
        sad = (emotion or [0.0] * 5)[2] if emotion else 0.0

        def make_frame(idx: int, window: list[float]) -> dict:
            rms = math.sqrt(sum(v * v for v in window) / len(window)) if window else 0.0
            jaw = min(1.0, rms * 6.0)
            blink = 1.0 if (idx % (FPS * 3) < 2) else 0.0  # ~1 blink / 3 s
            frame = {name: 0.0 for name in ARKIT_52}
            frame["JawOpen"] = jaw
            frame["MouthFunnel"] = jaw * 0.3
            frame["EyeBlinkLeft"] = blink
            frame["EyeBlinkRight"] = blink
            frame["MouthSmileLeft"] = frame["MouthSmileRight"] = 0.4 * happy
            frame["MouthFrownLeft"] = frame["MouthFrownRight"] = 0.4 * sad
            frame["BrowInnerUp"] = 0.3 * sad
            return {"frame": idx, "t": round(idx / FPS, 4), "arkit": frame}

        carry: list[float] = []
        i = 0
        async for chunk in chunks:
            carry.extend(_pcm16_to_float(chunk))
            while len(carry) >= spf:
                window, carry = carry[:spf], carry[spf:]
                yield make_frame(i, window)
                i += 1
        if i == 0 and carry:  # a sub-frame utterance still produces one frame
            yield make_frame(0, carry)


class HelperBackend(_BackendBase):
    """Bridges to the compiled C++ ``a2f_stream`` helper, which runs the batch-1
    engine + blendshape solve and emits 68 ARKit coefficients per frame at 60
    FPS — INTERLEAVED with the incoming audio (streaming executors).

    The helper is a **single persistent process**: it is spawned lazily on the
    first utterance (``_ensure_proc``) and kept alive across utterances so the
    engine loads once and per-utterance latency ≈ inference. Its stdin/stdout is
    one shared pipe, so the whole pipe-touching critical section runs under a
    single-flight lock (``self._lock``) — bytes from concurrent/serial calls
    must not interleave. If the helper crashes (``IncompleteReadError`` / broken
    pipe) or goes silent past the per-read inactivity budget, we kill it, drain
    stderr, and set ``self._proc = None`` so the next call respawns lazily.

    Per utterance (inside the lock) two tasks run concurrently:

    * WRITER: consumes the caller's chunk iterator, resamples each chunk
      24 kHz PCM16 → 16 kHz float (a per-utterance ``soxr.ResampleStream`` so
      chunk boundaries don't click; the flushed tail is written at the end),
      and writes framed ``[u32 nSamples][f32…]`` messages, then ``[u32 0]``.
      Zero-length resampler outputs are skipped — an empty audio message would
      read as the end marker. Every message is a single ``write()`` + bounded
      ``drain()``, so the writer is always cancellable at a message boundary.
    * EAGER READER: reads stdout frame messages into an internal queue
      regardless of consumer pace — a full 64 KiB stdout pipe would deadlock
      the helper's frame write against our writer's drain. Each read is
      bounded (``HELPER_FIRST_TIMEOUT`` for the first read after a (re)spawn,
      ``HELPER_TIMEOUT`` inactivity for the rest).

    The generator yields frames from the queue as they arrive (still under the
    lock — streaming requires it; the eager reader is what protects the pipe
    from a slow consumer). ABORT: if the consumer abandons the generator
    mid-utterance (``aclose()`` / cancellation), the writer is cancelled at a
    message boundary, ``[u32 ABORT_MARKER]`` is written, the helper's remaining
    frames are drained to its done marker (bounded), and the SAME process stays
    in service — no respawn. Any doubt (drain timeout, pipe error, helper
    desync) falls back to kill + respawn.
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

    async def _write_msg(self, proc: asyncio.subprocess.Process, msg: bytes) -> None:
        """Write ONE whole wire message and drain (bounded). ``write()`` buffers
        the entire message synchronously before any await, so a cancellation
        landing in ``drain()`` still leaves the stream at a message boundary —
        the abort path depends on this atomicity."""
        assert proc.stdin
        proc.stdin.write(msg)
        await asyncio.wait_for(proc.stdin.drain(), timeout=HELPER_TIMEOUT)

    async def _pump_stdin(
        self,
        proc: asyncio.subprocess.Process,
        emo: list[float],
        chunks: AsyncIterator[bytes],
        state: _UtteranceState,
        queue: asyncio.Queue,
    ) -> None:
        """WRITER task: emotion header, then one framed audio message per
        resampled chunk, then the flushed resampler tail, then the ``[u32 0]``
        end marker. A failing chunk SOURCE aborts the utterance on the helper
        (which stays in service) and surfaces as ``_ChunkSourceError``; wire
        errors are also pushed to ``queue`` so the consumer loop wakes up even
        if the reader is stalled."""
        import numpy as np
        import soxr

        # Per-utterance streaming resampler: carries filter state across chunk
        # boundaries (a stateless per-chunk resample would click at the seams).
        resampler = soxr.ResampleStream(SAMPLE_RATE_IN, SAMPLE_RATE_MODEL, 1, dtype="float32")

        def audio_msg(chunk: bytes | None) -> bytes | None:
            """Resample one chunk (or flush the tail for ``None``) into a framed
            message; ``None`` result for zero-length output — NEVER write an
            empty audio message, ``[u32 0]`` means end-of-utterance."""
            x = (
                np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
                if chunk is not None
                else np.empty(0, dtype=np.float32)
            )
            out = resampler.resample_chunk(x, last=chunk is None).astype("<f4")
            if out.size == 0:
                return None
            return struct.pack("<I", out.size) + out.tobytes()

        try:
            state.emotion_sent = True  # write() below buffers it before any await
            await self._write_msg(
                proc, struct.pack("<I", len(emo)) + struct.pack(f"<{len(emo)}f", *emo)
            )
            source = chunks.__aiter__()
            while True:
                try:
                    chunk = await source.__anext__()
                except StopAsyncIteration:
                    break
                except asyncio.CancelledError:
                    raise
                except BaseException as exc:
                    # The chunk source failed mid-utterance (TTS died): abort
                    # the utterance on the helper — it stays in service — and
                    # surface the original error to the consumer.
                    state.abort_sent = True
                    await self._write_msg(proc, struct.pack("<I", ABORT_MARKER))
                    raise _ChunkSourceError(exc) from exc
                msg = audio_msg(chunk)
                if msg is not None:
                    await self._write_msg(proc, msg)
            msg = audio_msg(None)  # flush the resampler's lookahead tail
            if msg is not None:
                await self._write_msg(proc, msg)
            state.end_sent = True
            await self._write_msg(proc, struct.pack("<I", 0))
        except (asyncio.CancelledError, _ChunkSourceError):
            raise
        except BaseException as exc:
            queue.put_nowait(exc)  # wake the consumer loop → it runs the respawn path
            raise

    async def _pump_stdout(
        self,
        proc: asyncio.subprocess.Process,
        queue: asyncio.Queue,
        stride: int,
        first: bool,
    ) -> None:
        """EAGER READER task: drains helper stdout into the unbounded queue
        regardless of consumer pace, pushing every ``stride``-th frame as a
        dict, ``_DONE`` on the done marker, or the exception on any failure.
        The first read after a (re)spawn is bounded by ``HELPER_FIRST_TIMEOUT``
        (it pays the one-time engine load); every later read by
        ``HELPER_TIMEOUT`` (inactivity)."""
        from arkit import ARKIT_68

        assert proc.stdout
        timeout = HELPER_FIRST_TIMEOUT if first else HELPER_TIMEOUT
        src_i = out_i = 0
        try:
            while True:
                hdr = await asyncio.wait_for(proc.stdout.readexactly(4), timeout=timeout)
                timeout = HELPER_TIMEOUT  # the engine-load budget applies once
                (n,) = struct.unpack("<I", hdr)
                if n == 0:
                    queue.put_nowait(_DONE)
                    return
                data = await asyncio.wait_for(proc.stdout.readexactly(n * 4), timeout=timeout)
                if src_i % stride == 0:
                    vals = struct.unpack(f"<{n}f", data)
                    queue.put_nowait(
                        {
                            "frame": out_i,
                            "t": round(out_i / FPS, 4),
                            "arkit": dict(zip(ARKIT_68, vals)),
                        }
                    )
                    out_i += 1
                src_i += 1
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            queue.put_nowait(exc)
            raise

    async def _recover_after_abandon(
        self,
        proc: asyncio.subprocess.Process,
        writer: asyncio.Task,
        reader: asyncio.Task,
        state: _UtteranceState,
    ) -> None:
        """The consumer abandoned the generator mid-utterance (``aclose()`` /
        cancellation): return the helper to service WITHOUT a respawn. Cancels
        the writer (always at a stdin message boundary — see ``_write_msg``),
        sends the abort marker if the utterance is still open, then lets the
        still-running EAGER READER drain the helper's remaining frames to the
        done marker (bounded — never a raw stdout read here, so no message can
        be torn mid-read). Raises on any boundary doubt; the caller then kills
        + clears so the next call respawns."""
        writer.cancel()
        await asyncio.gather(writer, return_exceptions=True)
        if not state.emotion_sent:
            # Nothing hit the wire — the helper never left the utterance
            # boundary; no frames are coming, just retire the reader.
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            return
        if not (state.end_sent or state.abort_sent):
            if reader.done():
                # done marker (or an error) before end-of-utterance was written
                # — the wire is not where the protocol says it is.
                raise RuntimeError("a2f_stream done before end-of-utterance — boundary uncertain")
            await self._write_msg(proc, struct.pack("<I", ABORT_MARKER))
        # The reader returns right after consuming the done marker (discarded
        # frames simply pile into the abandoned queue); it re-raises any pipe
        # error / inactivity timeout it hit instead.
        await asyncio.wait_for(reader, timeout=ABORT_DRAIN_TIMEOUT)

    async def stream_from(
        self, chunks: AsyncIterator[bytes], emotion: list[float] | None
    ) -> AsyncGenerator[dict, None]:
        emo = emotion if emotion else [0.0] * 10
        stride = max(1, 60 // FPS)  # helper is 60 FPS native; emit every `stride`th

        async with self._lock:
            existing = self._proc
            proc = await self._ensure_proc()
            state = _UtteranceState()
            queue: asyncio.Queue = asyncio.Queue()
            writer = asyncio.create_task(self._pump_stdin(proc, emo, chunks, state, queue))
            reader = asyncio.create_task(
                self._pump_stdout(proc, queue, stride, first=proc is not existing)
            )
            try:
                while True:
                    item = await queue.get()
                    if item is _DONE:
                        break
                    if isinstance(item, BaseException):
                        raise item
                    yield item
                await writer  # surfaces _ChunkSourceError (the abort already drained)
            except (GeneratorExit, asyncio.CancelledError):
                # Consumer abandoned the stream mid-utterance (WS disconnect /
                # barge-in aclose()): abort the utterance and keep the helper in
                # service; on any doubt kill + clear so the next call respawns.
                try:
                    await self._recover_after_abandon(proc, writer, reader, state)
                except BaseException:  # noqa: BLE001
                    await self._terminate(proc)
                    self._proc = None
                raise
            except _ChunkSourceError as exc:
                # The writer aborted the utterance on the helper and the done
                # marker was consumed — the helper stays in service; the chunk
                # source's own error propagates to the consumer.
                raise exc.original
            except (
                asyncio.IncompleteReadError,
                BrokenPipeError,
                ConnectionResetError,
                asyncio.TimeoutError,
            ) as exc:
                # Helper crashed (pipe) or went silent past the inactivity
                # budget (timeout) → kill, salvage stderr from the pipe buffer,
                # clear so the next call respawns clean.
                await self._terminate(proc)
                self._proc = None
                err = ""
                if proc.stderr is not None:
                    try:
                        err = (await asyncio.wait_for(proc.stderr.read(), timeout=1.0)).decode()
                    except Exception:  # noqa: BLE001
                        err = ""
                reason = "timed out" if isinstance(exc, asyncio.TimeoutError) else "ended early"
                raise RuntimeError(f"a2f_stream {reason}: {err.strip()[:300]}")
            except BaseException:
                # Any other error may leave stdin half-fed or stdout half-drained
                # → the wire is desynced. Kill + clear so the next call respawns,
                # but let the original exception propagate.
                await self._terminate(proc)
                self._proc = None
                raise
            finally:
                writer.cancel()
                reader.cancel()
                await asyncio.gather(writer, reader, return_exceptions=True)

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
