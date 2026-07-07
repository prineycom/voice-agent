#!/usr/bin/env python3
"""A2F emotion-vector calibration probe (issue #39).

Standalone CLI that talks read-only to the production Desktop services to run
the A2F calibration experiment: capture one fixed utterance from TTS, replay it
into the A2F service under different emotion vectors, and compare the resulting
ARKit blendshape streams quantitatively.

Subcommands:
    tts      Capture the fixed utterance WAV from the production TTS service.
    run      Stream a WAV into the A2F service with a chosen emotion vector and
             save the returned blendshape frames (directly `window.__a2fInject`-able).
    analyze  Compare 2+ run JSONs against a baseline, grouped by face region.

Examples:
    a2f_probe.py tts --out /tmp/utterance.wav
    a2f_probe.py run --wav /tmp/utterance.wav --preset zeros --out zeros.json
    a2f_probe.py run --wav /tmp/utterance.wav --preset joy --out joy.json
    a2f_probe.py run --wav /tmp/utterance.wav --emotion "joy=1.0,anger=0.3" --out mix.json
    a2f_probe.py analyze zeros.json joy.json mix.json --baseline zeros.json

Zero dependencies beyond the agent venv (websockets, numpy); WAV I/O via stdlib.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import urllib.request
import wave
from datetime import datetime, timezone
from typing import NoReturn

import numpy as np
import websockets

# A2E emotion dimensions, in model order (verified infra/desktop/a2f/emotion.py:13-16).
A2E_DIMS = [
    "grief", "joy", "disgust", "outofbreath", "pain",
    "anger", "amazement", "cheekiness", "sadness", "fear",
]

DEFAULT_TTS_URL = "ws://100.75.88.35:8002/tts"
DEFAULT_A2F_URL = "ws://127.0.0.1:18003/a2f"
DEFAULT_TEXT = (
    "No — no, this can't be happening. After everything we built together, "
    "you're telling me it's all gone? That is absolutely unbelievable."
)

SAMPLE_RATE = 24000
PCM_CHUNK = 32768
RECV_TIMEOUT_S = 60.0  # first helper utterance after spawn can take ~30 s

# Blendshape groups for analysis. Grouping mirrors how the frontend consumes the
# keys (infra/pi/web/static/js/arkit-map.js:12-36), with eyes split into
# expressive / blink / gaze; jaw-articulation is the control group (driven by
# the audio, expected ~stable across emotion vectors).
GROUPS = {
    "brows": [
        "BrowInnerUp", "BrowOuterUpLeft", "BrowOuterUpRight", "BrowDownLeft", "BrowDownRight",
    ],
    "eyes-expressive": ["EyeWideLeft", "EyeWideRight", "EyeSquintLeft", "EyeSquintRight"],
    "eyes-blink": ["EyeBlinkLeft", "EyeBlinkRight"],
    "eyes-gaze": [
        "EyeLookInLeft", "EyeLookInRight", "EyeLookOutLeft", "EyeLookOutRight",
        "EyeLookUpLeft", "EyeLookUpRight", "EyeLookDownLeft", "EyeLookDownRight",
    ],
    "mouth-form": [
        "MouthSmileLeft", "MouthSmileRight", "MouthFrownLeft", "MouthFrownRight", "MouthPucker",
    ],
    "cheeks": ["CheekSquintLeft", "CheekSquintRight"],
    "jaw-articulation": ["JawOpen", "JawForward", "JawLeft", "JawRight", "MouthClose"],
}


def fail(msg: str) -> NoReturn:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# tts — capture the fixed utterance WAV from production TTS
# ---------------------------------------------------------------------------

async def _tts_capture(url: str, text: str, voice: str) -> bytes:
    """One TTS request → concatenated PCM16 @ 24 kHz mono bytes."""
    pcm = bytearray()
    # Mirrors the production client loop (infra/desktop/tts/a2f_fork.py:103-131).
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"text": text, "voice": voice, "emotion": "neutral"}))
        while True:
            msg = await ws.recv()
            if isinstance(msg, (bytes, bytearray)):
                pcm += msg
                continue
            data = json.loads(msg)
            if "error" in data:
                fail(f"TTS error: {data['error']}")
            if data.get("done"):
                break
            # The TTS socket tees into A2F and interleaves TEXT frames like
            # {"type":"blendshapes",...} / {"type":"a2f_done"} on the same
            # connection (infra/desktop/tts/server.py:157-158,217) — discard.
    return bytes(pcm)


def cmd_tts(args: argparse.Namespace) -> None:
    try:
        pcm = asyncio.run(asyncio.wait_for(_tts_capture(args.url, args.text, args.voice), RECV_TIMEOUT_S))
    except asyncio.TimeoutError:
        fail(f"TTS capture timed out after {RECV_TIMEOUT_S:.0f} s")
    except OSError as e:
        fail(f"TTS connection failed: {e}")
    if not pcm:
        fail("TTS returned no audio")

    with wave.open(args.out, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)

    duration = len(pcm) / 2 / SAMPLE_RATE
    sha = hashlib.sha256(pcm).hexdigest()
    print(f"wav: {args.out}")
    print(f"duration_s: {duration:.3f}")
    print(f"pcm_sha256: {sha}")
    if not 4.0 <= duration <= 10.0:
        fail(f"duration {duration:.3f} s outside 4-10 s (experiment needs ~5-8 s)")


# ---------------------------------------------------------------------------
# run — one A2F capture
# ---------------------------------------------------------------------------

def build_emotion_vector(
    preset: str | None = None,
    vector: str | None = None,
    emotion: str | None = None,
) -> list[float]:
    """Build the 10-float A2E emotion vector from exactly one CLI source. Pure."""
    given = [v for v in (preset, vector, emotion) if v is not None]
    if len(given) != 1:
        fail("exactly one of --preset / --vector / --emotion is required")
    if preset is not None:
        vec = [0.0] * len(A2E_DIMS)
        if preset == "joy":
            vec[A2E_DIMS.index("joy")] = 1.0
        elif preset == "anger":
            vec[A2E_DIMS.index("anger")] = 1.0
        elif preset != "zeros":
            fail(f"unknown preset {preset!r}")
        return vec
    if vector is not None:
        try:
            vec = [float(x) for x in vector.split(",")]
        except ValueError:
            fail(f"--vector must be comma-separated floats, got {vector!r}")
        if len(vec) != len(A2E_DIMS):
            fail(f"--vector needs {len(A2E_DIMS)} floats, got {len(vec)}")
        return vec
    vec = [0.0] * len(A2E_DIMS)
    for pair in emotion.split(","):
        name, sep, value = pair.partition("=")
        name = name.strip()
        if not sep or name not in A2E_DIMS:
            fail(f"--emotion pair {pair!r} must be <dim>=<float> with dim in {A2E_DIMS}")
        try:
            vec[A2E_DIMS.index(name)] = float(value)
        except ValueError:
            fail(f"--emotion value in {pair!r} is not a float")
    return vec


def _health_url(ws_url: str) -> str:
    from urllib.parse import urlsplit

    parts = urlsplit(ws_url)
    return f"http://{parts.netloc}/health"


def _check_health(ws_url: str, allow_mock: bool) -> dict:
    url = _health_url(ws_url)
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            health = json.loads(resp.read())
    except Exception as e:  # noqa: BLE001 — any transport/parse failure is fatal here
        fail(f"health check failed ({url}): {e}")
    backend = health.get("backend")
    if backend != "helper" and not allow_mock:
        fail(f"A2F backend is {backend!r}, not 'helper' — pass --allow-mock to proceed anyway")
    return health


def _read_wav(path: str) -> tuple[bytes, float]:
    """Read a WAV, assert PCM16 @ 24 kHz mono, return (pcm_bytes, duration_s)."""
    try:
        with wave.open(path, "rb") as wf:
            if (wf.getnchannels(), wf.getsampwidth(), wf.getframerate()) != (1, 2, SAMPLE_RATE):
                fail(
                    f"{path}: expected 1ch/16-bit/{SAMPLE_RATE} Hz, got "
                    f"{wf.getnchannels()}ch/{wf.getsampwidth() * 8}-bit/{wf.getframerate()} Hz"
                )
            pcm = wf.readframes(wf.getnframes())
    except (OSError, wave.Error) as e:
        fail(f"cannot read WAV {path}: {e}")
    return pcm, len(pcm) / 2 / SAMPLE_RATE


async def _a2f_capture(url: str, pcm: bytes, emotion_vector: list[float]) -> list[dict]:
    """One A2F utterance → the blendshape frames, verbatim as received."""
    frames: list[dict] = []
    # Protocol per infra/desktop/a2f/server.py:79-109; client loop mirrors
    # infra/desktop/tts/a2f_fork.py:103-131.
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"emotion": emotion_vector}))
        for i in range(0, len(pcm), PCM_CHUNK):
            await ws.send(pcm[i : i + PCM_CHUNK])
        await ws.send(json.dumps({"end": True}))
        while True:
            msg = await ws.recv()
            if not isinstance(msg, str):
                continue
            data = json.loads(msg)
            if data.get("type") == "blendshapes":
                frames.append(data)  # keep {type, frame, t, arkit} untouched
            elif "error" in data:
                fail(f"A2F error: {data['error']}")
            elif data.get("done"):
                break
    return frames


def cmd_run(args: argparse.Namespace) -> None:
    emotion_vector = build_emotion_vector(args.preset, args.vector, args.emotion)
    label = args.label or args.preset or args.emotion or "vector"

    health = _check_health(args.url, args.allow_mock)
    pcm, duration = _read_wav(args.wav)
    wav_sha256 = hashlib.sha256(pcm).hexdigest()

    try:
        frames = asyncio.run(
            asyncio.wait_for(_a2f_capture(args.url, pcm, emotion_vector), RECV_TIMEOUT_S)
        )
    except asyncio.TimeoutError:
        fail(f"A2F capture timed out after {RECV_TIMEOUT_S:.0f} s")
    except OSError as e:
        fail(f"A2F connection failed: {e}")

    out = {
        "meta": {
            "issue": 39,
            "label": label,
            "emotion_vector": emotion_vector,
            "a2e_dims": A2E_DIMS,
            "url": args.url,
            "wav_path": args.wav,
            "wav_sha256": wav_sha256,
            "wav_duration_s": round(duration, 3),
            "frame_count": len(frames),
            "fps_nominal": 30,
            "health": health,
            "backend": health.get("backend"),
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "frames": frames,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f)
    print(
        f"run '{label}': {len(frames)} frames, {duration:.3f} s audio, "
        f"backend={health.get('backend')} -> {args.out}"
    )


# ---------------------------------------------------------------------------
# analyze — quantitative comparison of run JSONs
# ---------------------------------------------------------------------------

def _load_run(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            run = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        fail(f"cannot load run JSON {path}: {e}")
    if "meta" not in run or "frames" not in run:
        fail(f"{path}: not a probe run JSON (missing meta/frames)")
    run["_path"] = path
    return run


def _key_stats(frames: list[dict], key: str) -> tuple[float, float, float] | None:
    """(mean, max, std) of one blendshape over frames; None if the key never appears."""
    values = [f["arkit"][key] for f in frames if key in f.get("arkit", {})]
    if not values:
        return None
    arr = np.asarray(values, dtype=np.float64)
    return float(arr.mean()), float(arr.max()), float(arr.std())


def _group_stats(frames: list[dict], keys: list[str]) -> tuple[float, float, float] | None:
    """(mean-of-means, max-of-maxes, mean-of-stds) over the group's present keys."""
    per_key = [s for s in (_key_stats(frames, k) for k in keys) if s is not None]
    if not per_key:
        return None
    means, maxes, stds = zip(*per_key)
    return float(np.mean(means)), float(max(maxes)), float(np.mean(stds))


def _ratio(run_mean: float, base_mean: float) -> str:
    if base_mean == 0.0:
        return "n/a" if run_mean == 0.0 else "∞"
    return f"{run_mean / base_mean:.4f}"


def cmd_analyze(args: argparse.Namespace) -> None:
    import os

    paths = list(args.runs)
    baseline_real = os.path.realpath(args.baseline)
    if baseline_real not in {os.path.realpath(p) for p in paths}:
        paths.insert(0, args.baseline)
    if len(paths) < 2:
        fail("analyze needs at least 2 distinct runs (baseline included)")
    runs = [_load_run(p) for p in paths]
    baseline = next(r for r in runs if os.path.realpath(r["_path"]) == baseline_real)

    shas = {r["meta"].get("wav_sha256") for r in runs}
    if len(shas) != 1:
        detail = ", ".join(f"{r['_path']}={r['meta'].get('wav_sha256')}" for r in runs)
        fail(f"runs do not share the same wav_sha256 — not comparable: {detail}")

    grouped_keys = {k for keys in GROUPS.values() for k in keys}
    all_keys = {k for r in runs for f in r["frames"] for k in f.get("arkit", {})}
    tongue_keys = sorted(k for k in all_keys if k.startswith("Tongue"))
    other_keys = sorted(all_keys - grouped_keys - set(tongue_keys))

    print("# A2F probe analysis (issue #39)")
    print()
    print(f"Baseline: **{baseline['meta']['label']}** ({baseline['_path']})")
    print()
    print("## Runs")
    print()
    print("| label | emotion vector | frames | backend |")
    print("|---|---|---|---|")
    for r in runs:
        m = r["meta"]
        vec = ", ".join(f"{v:g}" for v in m.get("emotion_vector", []))
        print(f"| {m['label']} | [{vec}] | {m.get('frame_count', len(r['frames']))} | {m.get('backend')} |")
    print()
    print(
        f"Excluded from grouping: {len(tongue_keys)} Tongue* keys"
        + (f"; {len(other_keys)} other ungrouped keys" if other_keys else "")
        + "."
    )

    for group, keys in GROUPS.items():
        print()
        note = " (control — expected ~stable across runs)" if group == "jaw-articulation" else ""
        print(f"## {group}{note}")
        print()
        print("| run | mean | max | std | Δmean vs baseline | ratio |")
        print("|---|---|---|---|---|---|")
        base_stats = _group_stats(baseline["frames"], keys)
        for r in runs:
            stats = _group_stats(r["frames"], keys)
            if stats is None:
                print(f"| {r['meta']['label']} | n/a | n/a | n/a | n/a | n/a |")
                continue
            mean, mx, std = stats
            if base_stats is None:
                delta, ratio = "n/a", "n/a"
            else:
                delta = f"{mean - base_stats[0]:+.4f}"
                ratio = _ratio(mean, base_stats[0])
            print(f"| {r['meta']['label']} | {mean:.4f} | {mx:.4f} | {std:.4f} | {delta} | {ratio} |")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="a2f_probe",
        description="A2F emotion-vector calibration probe (issue #39).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_tts = sub.add_parser("tts", help="capture the fixed utterance WAV from production TTS")
    p_tts.add_argument("--url", default=DEFAULT_TTS_URL, help=f"TTS WS URL (default {DEFAULT_TTS_URL})")
    p_tts.add_argument("--text", default=DEFAULT_TEXT, help="utterance text")
    p_tts.add_argument("--voice", default="default", help="TTS voice (default 'default')")
    p_tts.add_argument("--out", required=True, help="output WAV path")
    p_tts.set_defaults(func=cmd_tts)

    p_run = sub.add_parser("run", help="one A2F capture with a chosen emotion vector")
    p_run.add_argument("--url", default=DEFAULT_A2F_URL, help=f"A2F WS URL (default {DEFAULT_A2F_URL})")
    p_run.add_argument("--wav", required=True, help="input WAV (PCM16 mono 24 kHz)")
    p_run.add_argument("--out", required=True, help="output run JSON path")
    emo = p_run.add_mutually_exclusive_group(required=True)
    emo.add_argument("--preset", choices=["zeros", "joy", "anger"], help="named emotion preset")
    emo.add_argument("--vector", help='10 comma-separated floats, e.g. "0,1,0,0,0,0,0,0,0,0"')
    emo.add_argument("--emotion", help='name=value pairs on A2E dims, e.g. "joy=1.0,anger=0.3"')
    p_run.add_argument("--label", help="run label (default: derived from the emotion args)")
    p_run.add_argument("--allow-mock", action="store_true", help="proceed even if backend != 'helper'")
    p_run.set_defaults(func=cmd_run)

    p_an = sub.add_parser("analyze", help="compare 2+ run JSONs against a baseline")
    p_an.add_argument("runs", nargs="+", help="run JSON paths")
    p_an.add_argument("--baseline", required=True, help="baseline run JSON path")
    p_an.set_defaults(func=cmd_analyze)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
