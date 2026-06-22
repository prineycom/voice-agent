"""Smoke test: stream a 16kHz mono WAV to /stt and print transcripts.

Usage: python smoke_stt.py path/to/16k_mono.wav [ws://host:8001/stt]
"""

import asyncio
import json
import sys
import wave

import websockets


async def main(wav_path: str, url: str = "ws://localhost:8001/stt"):
    wf = wave.open(wav_path, "rb")
    assert wf.getframerate() == 16000, f"need 16kHz, got {wf.getframerate()}"
    assert wf.getnchannels() == 1, "need mono"
    assert wf.getsampwidth() == 2, "need 16-bit"
    pcm = wf.readframes(wf.getnframes())
    wf.close()

    async with websockets.connect(url, max_size=None) as ws:
        chunk = 1024  # ~32ms
        for i in range(0, len(pcm), chunk):
            await ws.send(pcm[i : i + chunk])
        await ws.send(json.dumps({"event": "end"}))
        while True:
            msg = await ws.recv()
            if isinstance(msg, bytes):
                continue
            data = json.loads(msg)
            print(data)
            if data.get("is_final") or data.get("error"):
                break


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:]))
