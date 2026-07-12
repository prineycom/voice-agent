#!/usr/bin/env python3
"""Generate 16 kHz test WAV clips via the Desktop TTS service (dev helper).

The #57 harness needs recorded clips to stream through the classifier. Until the
primary user records their own voice, this synthesises stand-in clips through the
same Desktop CustomVoice TTS the agent speaks with: positive wake-word phrases
(`pos_*.wav`) and negative general-Russian sentences (`neg_*.wav`). Filenames use
the `pos_`/`neg_` prefixes `detect.py` self-checks on.

These synthetic clips validate the *pipeline* (harness + model + threshold), not
real-user recall — TTS pronunciation is cleaner and less varied than a live
speaker, so treat a pass here as necessary-but-not-sufficient. Record real user
clips for the acceptance numbers.

TTS `/tts` contract (infra/desktop/tts/server.py): send JSON `{"text","voice"}`,
receive binary PCM16 @ 24 kHz mono chunks, then JSON `{"done": true}`. We
down-sample 24 k → 16 k (linear) and write mono 16-bit WAVs.

Usage:
    python make_test_clips.py                         # default phrase set → ../clips
    python make_test_clips.py --out ../clips --voice ryan
    python make_test_clips.py --ws ws://100.75.88.35:8002/tts
"""

from __future__ import annotations

import argparse
import asyncio
import json
import wave
from pathlib import Path

import numpy as np
import websockets

SRC_RATE = 24000
DST_RATE = 16000

# (filename-stem, text). Naming drives detect.py's self-check:
#   neg_*  MUST NOT fire — general Russian, incl. phonetic near-misses ("привет",
#          "принеси", "джаз") that stress a short-name model. This is the reliable,
#          reproducible regression (the "no false trip" acceptance criterion).
#   ref_*  reference wake utterances — informational only, never fail the run.
#          The Desktop CustomVoice TTS is a Russian speaker and NON-DETERMINISTIC
#          (see memory desktop-tts-qwen-sampling-seed): its rendering of the
#          English-trained "хей джарвис" swings run-to-run (0.0–0.4), so a synthetic
#          positive is not a dependable signal. Real positive acceptance needs the
#          primary user's own recorded clips — see README "Testing positives".
# NOTE: keep the wake phrase CONTIGUOUS (no comma between "Хей" and "Джарвис") — a
# comma inserts a pause that pushes the two words outside the model's 2 s window and
# tanks the score (observed 0.42 → 0.00). Prinei/Prinya refs need a trained model to
# fire (none yet); they are kept as ref_ so the run still passes.
DEFAULT_PHRASES: list[tuple[str, str]] = [
    ("ref_hey_jarvis", "Хей Джарвис"),
    ("ref_hey_jarvis_request", "Хей Джарвис какая сегодня погода"),
    ("ref_prinei", "Приней"),
    ("ref_prinya", "Приня"),
    ("neg_greeting", "Привет, как дела сегодня?"),
    ("neg_bring", "Принеси мне пожалуйста стакан воды"),
    ("neg_weather", "Сегодня на улице довольно тёплая и ясная погода"),
    ("neg_story", "Вчера я долго гулял по парку и слушал музыку"),
    ("neg_jazz", "Мне очень нравится джаз по вечерам"),
    ("neg_numbers", "Один два три четыре пять шесть семь восемь"),
]


def resample_linear(pcm16: np.ndarray, src: int, dst: int) -> np.ndarray:
    """Linear-interpolate int16 PCM from `src` to `dst` Hz (mono).

    Linear resampling is coarse but entirely adequate for wake-word test clips
    (the model runs its own mel front-end); it avoids a scipy/librosa dependency
    so this helper installs with just websockets + numpy.
    """
    if src == dst or len(pcm16) == 0:
        return pcm16
    n_dst = int(round(len(pcm16) * dst / src))
    x_src = np.arange(len(pcm16))
    x_dst = np.linspace(0, len(pcm16) - 1, n_dst)
    return np.interp(x_dst, x_src, pcm16.astype(np.float32)).astype(np.int16)


async def synth(ws_url: str, text: str, voice: str) -> np.ndarray:
    """Synthesise one phrase; return 16 kHz mono int16 PCM."""
    chunks: list[bytes] = []
    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.send(json.dumps({"text": text, "voice": voice}))
        while True:
            msg = await ws.recv()
            if isinstance(msg, (bytes, bytearray)):
                chunks.append(bytes(msg))
                continue
            data = json.loads(msg)
            if data.get("error"):
                raise RuntimeError(f"TTS error for {text!r}: {data['error']}")
            if data.get("done"):
                break
            # {"type": "a2f_done"} and blendshape frames are ignored here.
    pcm24 = np.frombuffer(b"".join(chunks), dtype=np.int16)
    return resample_linear(pcm24, SRC_RATE, DST_RATE)


def write_wav(path: Path, pcm16: np.ndarray) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(DST_RATE)
        wf.writeframes(pcm16.tobytes())


async def main_async(args: argparse.Namespace) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for stem, text in DEFAULT_PHRASES:
        pcm = await synth(args.ws, text, args.voice)
        path = out / f"{stem}.wav"
        write_wav(path, pcm)
        print(f"wrote {path.name:<28} {len(pcm) / DST_RATE:.2f}s  ({text})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ws", default="ws://100.75.88.35:8002/tts", help="Desktop TTS WebSocket URL.")
    ap.add_argument("--voice", default="ryan", help="CustomVoice speaker (default ryan).")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "clips"), help="Output dir.")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
