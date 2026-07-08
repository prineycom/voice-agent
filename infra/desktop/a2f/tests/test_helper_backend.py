"""GPU-free lifecycle tests for the REAL ``engine.HelperBackend`` (streaming).

Nothing else in the suite exercises ``HelperBackend`` (test_server.py runs the
mock backend). These tests drive the real spawn / single-flight lock /
interleaved framed read / abort / done-marker / crash-or-stall→respawn logic
against ``fake_a2f_stream.py`` — a pure Python subprocess that speaks the same
streaming stdin/stdout wire protocol as the compiled C++ helper — so no GPU and
no TensorRT are required.

The only heavy dependency is ``soxr`` (CPU resample) + ``numpy``, both real.
"""

import asyncio
import os
import struct
import sys
from unittest.mock import patch

import pytest

# --- env BEFORE importing engine (mirror test_server.py's env-before-import) ---
# NB: we do NOT set A2F_BACKEND=helper — these tests instantiate
# ``engine.HelperBackend`` directly, so ``make_backend()`` is never consulted, and
# setting it would leak into test_server.py (same process → its ``setdefault``
# would keep "helper" and break its mock-backend health assertion).
_HERE = os.path.dirname(__file__)
_FAKE = os.path.join(_HERE, "fake_a2f_stream.py")
os.environ["A2F_FPS"] = "30"  # stride = 60 // 30 = 2
# The engine execs ``A2F_HELPER`` as a single binary (no argv). We point it at the
# fake script and rely on its shebang + executable bit to launch it.
os.environ["A2F_HELPER"] = _FAKE

sys.path.insert(0, os.path.dirname(_HERE))
os.chmod(_FAKE, 0o755)  # guarantee executability regardless of checkout perms

import engine  # noqa: E402
from arkit import ARKIT_68  # noqa: E402

STRIDE = 60 // engine.FPS  # 2
# Must match fake_a2f_stream.py.
CRASH_SENTINEL = -99.0
STALL_SENTINEL = -111.0

# 1 s of PCM16 @ 24 kHz (values irrelevant — the fake ignores audio content).
# soxr resamples it to EXACTLY 16000 model samples (rational 2:3 ratio, tail
# flushed), the fake emits total*60//16000 = 60 native frames → 30 kept.
_PCM = struct.pack("<24000h", *([0] * 24000))
# Half an utterance: 12000 samples → ~7830 model samples after the streaming
# resampler (filter delay holds back the tail) → ≥ 1 native frame on its own.
_PCM_HALF = struct.pack("<12000h", *([0] * 12000))
NATIVE_FRAMES = 60
EXPECTED_FRAMES = NATIVE_FRAMES // STRIDE  # 30


async def _collect(backend, emotion):
    return [frame async for frame in backend.stream(_PCM, emotion)]


async def _shutdown(backend):
    """Cleanly stop the persistent helper (closing its stdin → the fake exits 0)
    so no orphan subprocess outlives the test's event loop."""
    proc = backend._proc
    if proc is not None and proc.returncode is None:
        try:
            proc.stdin.close()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except Exception:
            proc.kill()
            await proc.wait()


def _assert_valid_frames(frames):
    assert len(frames) == EXPECTED_FRAMES, f"want {EXPECTED_FRAMES}, got {len(frames)}"
    for i, frame in enumerate(frames):
        assert frame["frame"] == i
        assert set(frame["arkit"].keys()) == set(ARKIT_68)
        # native frame index survives the pipe; downsample keeps every STRIDE-th
        assert frame["arkit"]["EyeBlinkLeft"] == float(i * STRIDE)


def test_one_spawn_across_two_utterances():
    """Two utterances on ONE persistent process: spawn happens exactly once and
    the process object is stable across both calls."""
    backend = engine.HelperBackend()

    async def scenario():
        try:
            first = await _collect(backend, None)
            proc_after_first = backend._proc
            second = await _collect(backend, None)
            proc_after_second = backend._proc
            return first, second, proc_after_first, proc_after_second
        finally:
            await _shutdown(backend)

    with patch("asyncio.create_subprocess_exec", wraps=asyncio.create_subprocess_exec) as spawn:
        first, second, p1, p2 = asyncio.run(scenario())

    assert spawn.call_count == 1  # spawned once, reused for the second utterance
    assert p1 is p2 and p1 is not None  # same persistent process
    _assert_valid_frames(first)
    _assert_valid_frames(second)


def test_done_marker_is_not_process_exit():
    """A done marker ends the utterance but keeps the process alive; the next
    utterance succeeds on the same live process."""
    backend = engine.HelperBackend()

    async def scenario():
        try:
            first = await _collect(backend, None)
            alive_after_first = backend._proc.returncode is None
            second = await _collect(backend, None)
            return first, second, alive_after_first
        finally:
            await _shutdown(backend)

    first, second, alive_after_first = asyncio.run(scenario())

    assert alive_after_first is True  # done marker != exit
    _assert_valid_frames(first)
    _assert_valid_frames(second)


def test_stride_downsample_and_arkit_keys():
    """60 native frames → 60 // stride kept, each an ARKIT_68 dict (mirrors the
    ARKIT assertion style of test_server.py)."""
    backend = engine.HelperBackend()

    async def scenario():
        try:
            return await _collect(backend, None)
        finally:
            await _shutdown(backend)

    frames = asyncio.run(scenario())

    assert len(frames) == NATIVE_FRAMES // STRIDE
    assert set(ARKIT_68).issubset(frames[0]["arkit"].keys())
    assert len(frames[0]["arkit"]) == 68
    _assert_valid_frames(frames)


def test_frames_interleave_with_chunk_arrival():
    """STREAMING pin: frames are yielded BEFORE the chunk iterator is exhausted.
    The source withholds the last chunk until the consumer has seen a frame —
    this can only complete if the helper computes per chunk. (The old
    whole-utterance engine would deadlock here: audio would end only after a
    frame, frames only after the audio end — the wait_for(10) turns that
    deadlock into a crisp failure.)"""
    backend = engine.HelperBackend()

    async def scenario():
        try:
            got_frame = asyncio.Event()

            async def chunks():
                yield _PCM_HALF
                await asyncio.wait_for(got_frame.wait(), timeout=10)
                yield _PCM_HALF

            frames = []
            async for frame in backend.stream_from(chunks(), None):
                got_frame.set()
                frames.append(frame)
            return frames
        finally:
            await _shutdown(backend)

    frames = asyncio.run(scenario())

    # Both halves resample to exactly 16000 model samples in total (the
    # streaming resampler's tail is flushed at end) → the full 60/30 contract.
    _assert_valid_frames(frames)


def test_crash_then_respawn():
    """A crashing utterance raises RuntimeError and clears the process; the next
    utterance transparently respawns a NEW process and succeeds."""
    backend = engine.HelperBackend()

    async def scenario():
        try:
            # 1. crash: sentinel emotion makes the fake break its pipe (EOF).
            with pytest.raises(RuntimeError):
                await _collect(backend, [CRASH_SENTINEL, 0.0, 0.0, 0.0, 0.0])
            cleared = backend._proc is None  # crash path sets _proc = None

            # 2. respawn: a normal utterance spawns a fresh process and succeeds.
            frames = await _collect(backend, None)
            return cleared, backend._proc, frames
        finally:
            await _shutdown(backend)

    with patch("asyncio.create_subprocess_exec", wraps=asyncio.create_subprocess_exec) as spawn:
        cleared, proc_after, frames = asyncio.run(scenario())

    assert cleared is True  # crash cleared the persistent process
    assert spawn.call_count == 2  # one spawn for the crash, one for the respawn
    assert proc_after is not None
    _assert_valid_frames(frames)


def test_consumer_abort_keeps_helper_in_service():
    """ABORT pin (DD-5): aclose() mid-utterance cancels the writer at a message
    boundary, sends the abort marker, drains the helper's done marker — and the
    SAME process object (no respawn) serves the next utterance."""
    backend = engine.HelperBackend()

    async def scenario():
        try:
            hold = asyncio.Event()  # never set — the source idles after chunk 1

            async def chunks():
                yield _PCM_HALF
                await hold.wait()  # cancelled by the writer teardown on abort

            agen = backend.stream_from(chunks(), None)
            first = await agen.__anext__()  # utterance is live mid-stream
            await agen.aclose()
            proc_after_abort = backend._proc

            frames = await _collect(backend, None)  # next utterance, same helper
            return first, proc_after_abort, backend._proc, frames
        finally:
            await _shutdown(backend)

    with patch("asyncio.create_subprocess_exec", wraps=asyncio.create_subprocess_exec) as spawn:
        first, p_abort, p_after, frames = asyncio.run(scenario())

    assert spawn.call_count == 1  # the abort did NOT respawn
    assert p_abort is not None and p_abort is p_after  # same process kept in service
    assert first["frame"] == 0 and first["arkit"]["EyeBlinkLeft"] == 0.0
    _assert_valid_frames(frames)


def test_stalled_helper_inactivity_timeout_respawns():
    """TIMEOUT pin (DD-6): a hung (not crashed) helper trips the per-READ
    inactivity timeout — even though earlier frames of the SAME utterance
    arrived fine (i.e. it is not a whole-utterance budget) — and is killed +
    cleared; the next utterance respawns."""
    backend = engine.HelperBackend()

    async def scenario():
        try:
            # The fake emits the first chunk's frames, then sleeps forever.
            with pytest.raises(RuntimeError, match="timed out"):
                await _collect(backend, [STALL_SENTINEL, 0.0, 0.0, 0.0, 0.0])
            cleared = backend._proc is None

            frames = await _collect(backend, None)
            return cleared, backend._proc, frames
        finally:
            await _shutdown(backend)

    with patch.object(engine, "HELPER_TIMEOUT", 0.3), patch(
        "asyncio.create_subprocess_exec", wraps=asyncio.create_subprocess_exec
    ) as spawn:
        cleared, proc_after, frames = asyncio.run(scenario())

    assert cleared is True  # inactivity timeout cleared the persistent process
    assert spawn.call_count == 2  # one for the stalled utterance, one respawn
    assert proc_after is not None
    _assert_valid_frames(frames)


def test_abort_with_unresponsive_helper_respawns():
    """Heir of the reuse-after-interruption desync pin: if the abort drain can't
    reach the done marker (helper hung mid-utterance), boundary certainty is
    lost — the process is killed and cleared, NEVER reused, and the next
    utterance respawns fresh with clean, non-garbled frames."""
    backend = engine.HelperBackend()

    async def scenario():
        try:
            agen = backend.stream(_PCM, [STALL_SENTINEL, 0.0, 0.0, 0.0, 0.0])
            await agen.__anext__()  # first-chunk frames arrived; the fake now hangs
            await agen.aclose()  # abort path: drain times out → kill + clear
            cleared = backend._proc is None

            frames = await _collect(backend, None)
            return cleared, backend._proc, frames
        finally:
            await _shutdown(backend)

    with patch.object(engine, "ABORT_DRAIN_TIMEOUT", 0.3), patch(
        "asyncio.create_subprocess_exec", wraps=asyncio.create_subprocess_exec
    ) as spawn:
        cleared, proc_after, frames = asyncio.run(scenario())

    assert cleared is True  # boundary uncertainty killed + cleared the process
    assert spawn.call_count == 2  # one for the aborted utterance, one respawn
    assert proc_after is not None
    _assert_valid_frames(frames)  # respawned wire is clean (not garbled/desynced)


def test_chunk_source_error_aborts_and_keeps_helper():
    """A chunk iterator that raises mid-utterance behaves like an abort: the
    original error reaches the consumer, and the helper stays in service (no
    respawn) for the next utterance."""
    backend = engine.HelperBackend()

    class SourceBoom(Exception):
        pass

    async def scenario():
        try:
            async def chunks():
                yield _PCM_HALF
                raise SourceBoom("tts died")

            with pytest.raises(SourceBoom):
                async for _ in backend.stream_from(chunks(), None):
                    pass
            proc_after_abort = backend._proc

            frames = await _collect(backend, None)
            return proc_after_abort, backend._proc, frames
        finally:
            await _shutdown(backend)

    with patch("asyncio.create_subprocess_exec", wraps=asyncio.create_subprocess_exec) as spawn:
        p_abort, p_after, frames = asyncio.run(scenario())

    assert spawn.call_count == 1  # the source failure did NOT respawn the helper
    assert p_abort is not None and p_abort is p_after
    _assert_valid_frames(frames)


def test_mock_backend_stream_from_matches_stream():
    """MockBackend shares the streaming API: chunked input through stream_from
    yields exactly the frames of the back-compat one-shot stream() wrapper."""
    mock = engine.MockBackend()
    emotion = [0.0, 1.0, 0.0, 0.0, 0.0]

    async def scenario():
        one_shot = [f async for f in mock.stream(_PCM, emotion)]

        async def chunks():
            for i in range(0, len(_PCM), 4096):
                yield _PCM[i : i + 4096]

        chunked = [f async for f in mock.stream_from(chunks(), emotion)]
        return one_shot, chunked

    one_shot, chunked = asyncio.run(scenario())

    assert len(one_shot) == engine.FPS  # 1 s of audio at 30 FPS
    assert one_shot == chunked
