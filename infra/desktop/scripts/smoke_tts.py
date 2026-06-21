"""Smoke test: send Russian text to /tts, save 24kHz WAV, report first-chunk latency.

Usage: python smoke_tts.py "текст" [out.wav] [ws://host:8002/tts]
"""

import asyncio
import json
import sys
import time
import wave

import websockets


async def main(text: str, out_path: str = "tts_out.wav", url: str = "ws://localhost:8002/tts"):
    async with websockets.connect(url, max_size=None) as ws:
        t0 = time.time()
        await ws.send(json.dumps({"text": text, "voice": "default"}))
        pcm = bytearray()
        first_ms = None
        while True:
            msg = await ws.recv()
            if isinstance(msg, bytes):
                if first_ms is None:
                    first_ms = (time.time() - t0) * 1000
                    print(f"first chunk: {first_ms:.0f} ms")
                pcm.extend(msg)
            else:
                data = json.loads(msg)
                if data.get("error"):
                    print("ERROR", data)
                    return
                if data.get("done"):
                    break
        with wave.open(out_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(bytes(pcm))
        print(f"wrote {out_path}: {len(pcm)} bytes (~{len(pcm)/2/24000:.1f}s)")


if __name__ == "__main__":
    args = sys.argv[1:]
    text = args[0] if args else "Привет! Это тест синтеза речи."
    asyncio.run(main(text, *args[1:]))
