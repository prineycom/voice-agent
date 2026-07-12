#!/usr/bin/env python3
"""Standalone wake-word test harness (off the agent).

Streams recorded WAV clips through the livekit-wakeword ONNX classifier exactly
the way the Pi agent gate does — a 2 s sliding window at 16 kHz with a 320 ms
stride — and prints per-window scores plus a hit/miss verdict per clip. This
proves detection on recorded audio *before* any agent integration (epic slice
#57), and is the tool you re-run after retraining to sanity-check a new model.

The model is livekit-wakeword's stateless `WakeWordModel`: mel-spectrogram and
Google speech-embedding front-ends are bundled in the package; only the small
wake-word classifier ONNX is passed here. openWakeWord classifiers (e.g. the
pretrained `hey_jarvis`) load through the same API — the package is backward
compatible.

Usage:
    # Score every WAV under ../clips against the packaged models, threshold 0.5
    python detect.py --models ../models/hey_jarvis.onnx ../clips/*.wav

    # A whole directory, custom threshold, show the winning window per clip
    python detect.py -m ../models/hey_jarvis.onnx -t 0.5 --clips ../clips

    # Multiple keyword models at once (multi-keyword gate, as the agent runs it)
    python detect.py -m ../models/hey_jarvis.onnx ../models/prinei.onnx -- clip.wav

Exit code is 0 when every clip whose filename starts with `pos_` fires and every
clip starting with `neg_` stays silent (a self-checking regression run); 1 on any
mismatch. Clips without that prefix are scored but never fail the run.

Requires only `livekit-wakeword` (numpy + onnxruntime); no torch, no GPU.
"""

from __future__ import annotations

import argparse
import glob
import sys
import wave
from pathlib import Path

import numpy as np

try:
    from livekit.wakeword import WakeWordModel
except ImportError:  # pragma: no cover - actionable install hint
    sys.exit(
        "livekit-wakeword is not installed. Install the inference deps:\n"
        "    pip install livekit-wakeword"
    )

SAMPLE_RATE = 16000
WINDOW = 32000  # 2 s at 16 kHz — yields exactly the 16 embeddings the model wants
STRIDE = 1280 * 4  # 320 ms — one embedding hop; matches examples/inference.py


def load_wav_16k_mono(path: Path) -> np.ndarray:
    """Load a WAV as int16 mono at 16 kHz.

    The harness deliberately does NOT resample: test clips are expected to be
    16 kHz mono PCM (what the agent gate feeds the model and what
    make_test_clips.py emits). A mismatch is a loud error rather than a silent
    garbled score.
    """
    with wave.open(str(path), "rb") as wf:
        if wf.getframerate() != SAMPLE_RATE:
            raise ValueError(
                f"{path.name}: {wf.getframerate()} Hz; expected {SAMPLE_RATE} Hz mono. "
                "Re-export at 16 kHz (make_test_clips.py already does)."
            )
        if wf.getnchannels() != 1:
            raise ValueError(f"{path.name}: {wf.getnchannels()} channels; expected mono.")
        if wf.getsampwidth() != 2:
            raise ValueError(f"{path.name}: sample width {wf.getsampwidth()}; expected 16-bit.")
        frames = wf.readframes(wf.getnframes())
    return np.frombuffer(frames, dtype=np.int16)


def scan_clip(model: WakeWordModel, audio: np.ndarray) -> dict[str, float]:
    """Slide the 2 s window over `audio` and return each model's PEAK score.

    Clips shorter than one window are zero-padded on the left so a short clip
    ("хей джарвис" alone is ~1 s) still presents a full 2 s window — the same
    thing the agent's rolling buffer does at the start of an utterance.
    """
    if len(audio) < WINDOW:
        audio = np.concatenate([np.zeros(WINDOW - len(audio), dtype=np.int16), audio])

    peak: dict[str, float] = {}
    for start in range(0, len(audio) - WINDOW + 1, STRIDE):
        scores = model.predict(audio[start : start + WINDOW])
        for name, score in scores.items():
            if score > peak.get(name, 0.0):
                peak[name] = score
    return peak


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-m", "--models", nargs="+", required=True, help="Wake-word classifier ONNX file(s).")
    ap.add_argument("-t", "--threshold", type=float, default=0.5, help="Hit threshold (default 0.5).")
    ap.add_argument("--clips", help="Directory of .wav clips to score (alternative to listing files).")
    ap.add_argument("wavs", nargs="*", help="WAV files to score (globs allowed).")
    args = ap.parse_args(argv)

    wavs: list[str] = []
    for pattern in args.wavs:
        wavs.extend(sorted(glob.glob(pattern)) or [pattern])
    if args.clips:
        wavs.extend(sorted(glob.glob(str(Path(args.clips) / "*.wav"))))
    wavs = [w for w in dict.fromkeys(wavs) if Path(w).is_file()]
    if not wavs:
        ap.error("no WAV clips found — pass files, a glob, or --clips DIR")

    model = WakeWordModel(models=args.models)
    names = [Path(m).stem for m in args.models]
    print(f"loaded {len(names)} model(s): {', '.join(names)}   threshold={args.threshold}\n")

    failures = 0
    for wav in wavs:
        name = Path(wav).name
        peak = scan_clip(model, load_wav_16k_mono(Path(wav)))
        best_model = max(peak, key=peak.get) if peak else "-"
        best_score = peak.get(best_model, 0.0)
        fired = best_score >= args.threshold
        verdict = "HIT " if fired else "miss"

        # Self-check: pos_* must fire, neg_* must not. Anything else is informational.
        expect = "pos" if name.startswith("pos_") else "neg" if name.startswith("neg_") else None
        mark = "  "
        if expect == "pos" and not fired:
            mark, failures = "!!", failures + 1
        elif expect == "neg" and fired:
            mark, failures = "!!", failures + 1
        elif expect is not None:
            mark = "ok"

        allscores = "  ".join(f"{k}={v:.3f}" for k, v in sorted(peak.items()))
        print(f"{mark} [{verdict}] {name:<34} peak {best_model}={best_score:.3f}   ({allscores})")

    if any(Path(w).name.startswith(("pos_", "neg_")) for w in wavs):
        print(f"\nself-check: {failures} mismatch(es)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
