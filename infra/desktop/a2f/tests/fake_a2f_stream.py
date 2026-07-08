#!/usr/bin/env python3
"""GPU-free stand-in for the compiled C++ ``a2f_stream`` helper.

Speaks the *exact* persistent STREAMING stdin/stdout wire protocol of
``a2f_stream/main.cpp`` that ``engine.HelperBackend`` writes and reads (see
``engine.py``), so the real ``HelperBackend`` lifecycle — lazy spawn,
single-flight lock, interleaved framed read, abort, done marker,
crash/stall→respawn — can be exercised without a GPU or TensorRT.

Wire protocol (all integers ``<I`` u32 little-endian, all coeffs/samples ``<f``
f32 little-endian), one *persistent* process across many utterances:

    stdin  (per utterance, written by the engine):
        [u32 emoLen][emoLen × f32 emotion]
        [u32 nSamples][nSamples × f32 audio @16kHz]   # one or more chunks
        then [u32 0]           # end-of-utterance marker
        or   [u32 0xFFFFFFFF]  # abort marker (no payload follows)
      On EOF while awaiting the next emotion header → clean shutdown (exit 0).

    stdout (per utterance, written by us):
        per frame [u32 68][68 × f32 coeffs], INTERLEAVED with the incoming
        chunks: after EVERY audio chunk we emit every 60 FPS frame the
        accumulated samples allow — ``floor(total_samples * 60 / 16000)`` so
        far — mirroring the real helper's streaming executors (this pins the
        interleaving: a chunk's frames are on the wire before the next chunk
        is read). Then [u32 0] done marker. On abort: NO further frames, just
        the done marker, then back to the next utterance's emotion header.

Determinism: native frame ``f`` has all 68 coefficients set to ``float(f)``.
The engine keeps every ``stride``-th native frame (``src_i % stride == 0``), so
callers can assert both counts and values (kept frame ``i`` carries value
``i * stride``). Total native frames per completed utterance =
``total_samples * 60 // 16000`` (no lookahead-tail simulation — the end marker
adds no extra frames).

Sentinels (first emotion value):

* ``CRASH_SENTINEL`` (-99.0): write a line to stderr, then CLOSE the raw
  stdout+stderr fds (EOF, before any frame) so the engine's read hits
  ``IncompleteReadError`` — the crash→respawn path. We then block on stdin so
  we stay alive for the engine's ``kill()`` + ``wait()``, keeping the crash
  path deterministic and free of the child-reaping race a self-``exit`` hits
  under repeated ``asyncio.run`` event loops. (Closing the raw fds via
  ``os.close`` — not the buffered ``sys.stdout`` objects — is what actually
  EOFs the parent's pipe ends.)

* ``STALL_SENTINEL`` (-111.0): after emitting the frames of the first audio
  chunk that produced any, sleep ``FAKE_A2F_STALL_SECS`` (default 30) without
  reading or writing — a hung (not crashed) helper, for the per-read
  inactivity-timeout and abort-drain-timeout paths.
"""

import os
import struct
import sys
import time

NATIVE_FPS = 60  # native frame rate of the real helper
MODEL_RATE = 16000  # the engine writes 16 kHz float audio
N_COEFFS = 68  # ARKIT_68 blendshape coefficients per frame
ABORT_MARKER = 0xFFFFFFFF
CRASH_SENTINEL = -99.0
STALL_SENTINEL = -111.0
STALL_SECS = float(os.getenv("FAKE_A2F_STALL_SECS", "30"))


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

        stall = bool(emo and emo[0] == STALL_SENTINEL)
        stalled = False

        # --- audio chunks: emit frames PER CHUNK until end/abort marker ------
        total_samples = 0
        emitted = 0
        while True:
            (n_samples,) = struct.unpack("<I", _read_exactly(stdin, 4))
            if n_samples == 0:
                break  # end-of-utterance
            if n_samples == ABORT_MARKER:
                break  # abort: no payload, no further frames — just the done marker
            _read_exactly(stdin, n_samples * 4)  # consume audio, value unused
            total_samples += n_samples
            want = total_samples * NATIVE_FPS // MODEL_RATE
            for f in range(emitted, want):
                stdout.write(struct.pack("<I", N_COEFFS))
                stdout.write(struct.pack(f"<{N_COEFFS}f", *([float(f)] * N_COEFFS)))
            stdout.flush()
            emitted = want
            if stall and emitted > 0 and not stalled:
                stalled = True
                time.sleep(STALL_SECS)  # hung helper: no reads, no writes

        # --- per-utterance done marker (the process stays alive) -------------
        stdout.write(struct.pack("<I", 0))
        stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
