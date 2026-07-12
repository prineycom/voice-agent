"""Tests for WakeWordDetector's pure bits — thresholding, hit selection, buffer.

The ONNX model is mocked (no onnxruntime load, no audio): these cover the
per-model threshold resolution, the "highest over threshold wins" hit rule, and
the fixed-size rolling window. The live audio loop (rtc.AudioStream) is exercised
end-to-end on hardware, not here.
"""

from unittest.mock import patch

import numpy as np

from wake_detector import WINDOW, WakeWordDetector


def _detector(**kw):
    hits = []
    with patch("livekit.wakeword.WakeWordModel"):
        det = WakeWordDetector(
            model_paths=["models/hey_jarvis.onnx", "models/prinei_ru.onnx"],
            on_detected=lambda name, score: hits.append((name, score)),
            **kw,
        )
    return det, hits


def test_threshold_for_uses_map_then_default():
    det, _ = _detector(thresholds={"hey_jarvis": 0.6}, default_threshold=0.4)
    assert det._threshold_for("hey_jarvis") == 0.6
    assert det._threshold_for("prinei_ru") == 0.4  # falls back to default


def test_check_fires_over_threshold():
    det, hits = _detector(default_threshold=0.5)
    det._check({"hey_jarvis": 0.72, "prinei_ru": 0.01})
    assert hits == [("hey_jarvis", 0.72)]


def test_check_silent_under_threshold():
    det, hits = _detector(default_threshold=0.5)
    det._check({"hey_jarvis": 0.49, "prinei_ru": 0.2})
    assert hits == []


def test_check_picks_highest_over_threshold():
    det, hits = _detector(default_threshold=0.5)
    det._check({"hey_jarvis": 0.61, "prinei_ru": 0.95})
    assert hits == [("prinei_ru", 0.95)]


def test_check_respects_per_model_threshold():
    det, hits = _detector(thresholds={"hey_jarvis": 0.9}, default_threshold=0.5)
    # 0.7 clears prinei_ru's default (0.5) but NOT hey_jarvis's 0.9.
    det._check({"hey_jarvis": 0.7, "prinei_ru": 0.7})
    assert hits == [("prinei_ru", 0.7)]


def test_append_keeps_last_window():
    det, _ = _detector()
    big = np.arange(WINDOW + 5000, dtype=np.int16)
    det._append(big)
    assert det._buf.shape == (WINDOW,)
    assert det._filled == WINDOW
    # Buffer holds the most-recent WINDOW samples.
    assert det._buf[-1] == big[-1]
    assert det._buf[0] == big[-WINDOW]


def test_append_accumulates_small_chunks():
    det, _ = _detector()
    for _ in range(4):
        det._append(np.ones(5000, dtype=np.int16))
    assert det._buf.shape == (WINDOW,)
    assert det._filled == 20000  # 4 * 5000, still short of a full window
