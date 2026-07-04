#!/usr/bin/env python3
"""GPU-free stand-in for the compiled C++ ``a2f_stream`` helper.

Speaks the *exact* persistent stdin/stdout wire protocol that
``engine.HelperBackend.stream`` writes and reads (see ``engine.py``), so the
real ``HelperBackend`` lifecycle — lazy spawn, single-flight lock, framed read,
done marker, crash→respawn — can be exercised without a GPU or TensorRT.

Wire protocol (all integers ``<I`` u32 little-endian, all coeffs/samples ``<f``
f32 little-endian), one *persistent* process across many utterances:

    stdin  (per utterance, written by the engine):
        [u32 emoLen][emoLen × f32 emotion]
        [u32 nSamples][nSamples × f32 audio]     # one or more audio chunks
        [u32 0]                                   # end-of-utterance marker
      On EOF while awaiting the next emotion header → clean shutdown (exit 0).

    stdout (per utterance, written by us):
        [u32 68][68 × f32 coeffs]  × NATIVE_FRAMES  # 60 FPS native frames
        [u32 0]                                     # done marker

Determinism: we always emit ``NATIVE_FRAMES`` frames per utterance. The engine
downsamples 60→A2F_FPS with ``stride = 60 // FPS`` (default FPS 30 → stride 2),
so a caller can assert ``NATIVE_FRAMES // stride`` post-stride frames. Every
coefficient of native frame ``f`` is set to ``float(f)`` (all 68 identical) so
the test can assert the value survives the pipe and that the first (kept) frame
is 0.0, the next is ``stride``, and so on.

Crash simulation: if the emotion vector's first value equals ``CRASH_SENTINEL``
(-99.0), write a line to stderr, then CLOSE the raw stdout+stderr fds (EOF, before
writing any frame) so the engine's read hits ``IncompleteReadError`` and its
``stderr.read()`` drain also EOFs — exactly the "helper ended early / broken pipe"
path in ``HelperBackend.stream``. We then block on stdin so we stay alive for the
engine's ``kill()`` + ``wait()``, which keeps the crash path deterministic and
free of the child-reaping race a self-``exit`` hits under repeated ``asyncio.run``
event loops. (Closing the raw fds via ``os.close`` — not the buffered
``sys.stdout`` objects — is what actually EOFs the parent's pipe ends.)
"""

import os
import struct
import sys

NATIVE_FRAMES = 60  # native 60 FPS frames emitted per utterance
N_COEFFS = 68  # ARKIT_68 blendshape coefficients per frame
CRASH_SENTINEL = -99.0


def _read_exactly(stream, n: int) -> bytes:
    """Read exactly ``n`` bytes or return b"" on clean EOF at a boundary."""
    buf = b""
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            # EOF: clean only if we're at a message boundary (nothing buffered).
            if not buf:
                return b""
            raise EOFError("unexpected EOF mid-message")
        buf += chunk
    return buf


def main() -> int:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer

    while True:
        # --- emotion header: EOF here is a clean shutdown ---------------------
        hdr = _read_exactly(stdin, 4)
        if hdr == b"":
            return 0  # stdin closed at a boundary → clean shutdown
        (emo_len,) = struct.unpack("<I", hdr)
        emo = list(struct.unpack(f"<{emo_len}f", _read_exactly(stdin, emo_len * 4))) if emo_len else []

        # --- crash simulation ------------------------------------------------
        if emo and emo[0] == CRASH_SENTINEL:
            os.write(2, b"fake_a2f_stream: crash sentinel received\n")
            os.close(2)  # EOF for the engine's stderr drain
            os.close(1)  # EOF → engine hits IncompleteReadError on its next stdout read
            try:
                stdin.read()  # stdin stays open → block here until the engine kills us
            except Exception:
                pass
            return 0

        # --- audio chunks until the end-of-utterance zero marker -------------
        while True:
            (n_samples,) = struct.unpack("<I", _read_exactly(stdin, 4))
            if n_samples == 0:
                break
            _read_exactly(stdin, n_samples * 4)  # consume audio, value unused

        # --- emit deterministic blendshape frames ----------------------------
        for f in range(NATIVE_FRAMES):
            coeffs = [float(f)] * N_COEFFS
            stdout.write(struct.pack("<I", N_COEFFS))
            stdout.write(struct.pack(f"<{N_COEFFS}f", *coeffs))
        stdout.write(struct.pack("<I", 0))  # done marker
        stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
