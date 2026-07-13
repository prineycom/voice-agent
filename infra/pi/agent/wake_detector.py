"""Server-side wake-word detector — the always-on gate on the Pi (ADR-0021 / #58).

Taps the user's incoming WebRTC audio track directly (NOT the STT path — STT is
non-streaming and only sees VAD-endpointed turns) and scores a rolling 2 s window
through the `livekit-wakeword` ONNX classifier. On a hit above the model's
threshold it calls back into `WakeState` to flip Dormant→Active.

Cheap by design: the classifier is ~75 ms per 2 s window on the Pi CPU, run on a
stride (default every 0.5 s) in a worker thread so it never blocks the event loop,
and skipped entirely while already Active (nothing to detect when the gate is
open) — so it costs well under 20 % of one core, in front of the heavy Desktop GPU
STT it gates.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from pathlib import Path
from typing import Callable

import numpy as np
from livekit import rtc

log = logging.getLogger("agent")

SAMPLE_RATE = 16000
WINDOW = 32000  # 2 s at 16 kHz — yields the 16 embeddings the classifier expects


class WakeWordDetector:
    """Scores a live audio track for wake words and reports hits to a callback.

    Args:
        model_paths: wake-word classifier ONNX file(s) — a multi-keyword gate.
        thresholds: per-model-name cutoff; ``default_threshold`` fills the rest.
        on_detected: called ``(name, score)`` on a hit (e.g. ``WakeState.on_wake_detected``).
        should_detect: return False to pause scoring (e.g. while already Active) —
            frames are still drained to avoid backpressure, only ``predict`` is skipped.
        stride_s: seconds of new audio between scorings.
    """

    def __init__(
        self,
        *,
        model_paths: list[str | Path],
        thresholds: dict[str, float] | None = None,
        default_threshold: float = 0.5,
        on_detected: Callable[[str, float], None],
        should_detect: Callable[[], bool] | None = None,
        stride_s: float = 0.5,
        debug: bool = False,
    ) -> None:
        # Import here so the whole agent doesn't hard-depend on livekit-wakeword
        # when the feature is disabled (WAKEWORD_ENABLED=0 never constructs this).
        from livekit.wakeword import WakeWordModel

        self._model = WakeWordModel(models=model_paths)
        self._names = [Path(p).stem for p in model_paths]
        self._thresholds = thresholds or {}
        self._default_threshold = default_threshold
        self._on_detected = on_detected
        self._should_detect = should_detect or (lambda: True)
        self._stride = int(stride_s * SAMPLE_RATE)
        self._debug = debug

        self._buf = np.zeros(WINDOW, dtype=np.int16)  # rolling 2 s window
        self._filled = 0  # samples seen (capped at WINDOW) — gate first predict
        self._since_predict = 0
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="wakeword"
        )
        self._task: asyncio.Task | None = None
        self._stream: rtc.AudioStream | None = None

    def _threshold_for(self, name: str) -> float:
        return self._thresholds.get(name, self._default_threshold)

    def start(self, track: rtc.Track) -> None:
        """Begin scoring ``track`` in a background task (idempotent per track)."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(track))
        log.info(
            "wake-word detector started: models=%s stride=%.2fs",
            ", ".join(self._names),
            self._stride / SAMPLE_RATE,
        )

    async def _run(self, track: rtc.Track) -> None:
        # AudioStream resamples the WebRTC track (typically 48 kHz) to 16 kHz mono
        # for us, so the classifier always sees the rate it was trained on.
        self._stream = rtc.AudioStream(
            track, sample_rate=SAMPLE_RATE, num_channels=1
        )
        loop = asyncio.get_running_loop()
        try:
            async for ev in self._stream:
                frame = getattr(ev, "frame", ev)
                samples = np.frombuffer(frame.data, dtype=np.int16)
                if samples.size == 0:
                    continue
                self._append(samples)
                self._since_predict += samples.size

                if self._since_predict < self._stride or self._filled < WINDOW:
                    continue
                self._since_predict = 0
                if not self._should_detect():
                    continue  # Active already — draining only, no scoring

                # Score off the event loop (predict is ~75 ms of CPU). A transient
                # predict failure must NOT kill the gate: log once and keep scoring
                # (otherwise one bad frame would deafen the agent for the session).
                try:
                    scores = await loop.run_in_executor(
                        self._executor, self._model.predict, self._buf.copy()
                    )
                    if self._debug and scores:
                        mx = max(scores.values())
                        if mx > 0.02:  # above the ~0.005 silence noise floor
                            log.info(
                                "wake-debug: %s (threshold %.2f)",
                                {k: round(v, 3) for k, v in scores.items()},
                                min(self._threshold_for(n) for n in scores),
                            )
                    self._check(scores)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("wake-word predict failed; skipping this window")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("wake-word detector loop crashed; gate is no longer running")

    def _append(self, samples: np.ndarray) -> None:
        """Slide ``samples`` into the fixed-size rolling window."""
        n = samples.size
        if n >= WINDOW:
            self._buf = samples[-WINDOW:].copy()
        else:
            self._buf = np.concatenate((self._buf[n:], samples))
        self._filled = min(WINDOW, self._filled + n)

    def _check(self, scores: dict[str, float]) -> None:
        """Fire ``on_detected`` for the highest-scoring model over its threshold."""
        best_name, best_score, best_over = None, 0.0, False
        for name, score in scores.items():
            if score >= self._threshold_for(name) and score > best_score:
                best_name, best_score, best_over = name, score, True
        if best_over and best_name is not None:
            self._on_detected(best_name, best_score)

    async def aclose(self) -> None:
        """Stop scoring and release the stream + worker thread."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._stream is not None:
            try:
                await self._stream.aclose()
            except Exception:
                pass
            self._stream = None
        self._executor.shutdown(wait=False)
