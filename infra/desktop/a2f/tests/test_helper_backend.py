"""GPU-free lifecycle tests for the REAL ``engine.HelperBackend``.

Nothing else in the suite exercises ``HelperBackend`` (test_server.py runs the
mock backend). These tests drive the real spawn / single-flight lock / framed
read / done-marker / crash→respawn logic against ``fake_a2f_stream.py`` — a pure
Python subprocess that speaks the same stdin/stdout wire protocol as the
compiled C++ helper — so no GPU and no TensorRT are required.

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
os.environ["A2F_FPS"] = "30"  # stride = 60 // 30 = 2 → 60 native frames → 30 kept
# The engine execs ``A2F_HELPER`` as a single binary (no argv). We point it at the
# fake script and rely on its shebang + executable bit to launch it.
os.environ["A2F_HELPER"] = _FAKE

sys.path.insert(0, os.path.dirname(_HERE))
os.chmod(_FAKE, 0o755)  # guarantee executability regardless of checkout perms

import engine  # noqa: E402
from arkit import ARKIT_68  # noqa: E402

# Must match fake_a2f_stream.py.
NATIVE_FRAMES = 60
STRIDE = 60 // engine.FPS  # 2
EXPECTED_FRAMES = NATIVE_FRAMES // STRIDE  # 30
CRASH_SENTINEL = -99.0

# Non-empty PCM16 @ 24 kHz (values irrelevant — the fake ignores audio content).
_PCM = struct.pack(f"<{2400}h", *([0] * 2400))


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
