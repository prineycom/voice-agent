"""Unit tests for A2FFork: ordering, audio-binding, cancel, failure isolation.

Drive the fork directly against the in-process fake /a2f server. Each test builds
the A2FFork inside the running loop (it schedules a task on construction) and tears
it down before stopping the fake server.
"""

import asyncio

from fake_a2f import FakeA2F

import a2f_fork


def _wait_for(predicate, timeout=5.0):
    """Poll the fake's recorded events until `predicate(events)` or timeout."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_emotion_and_pcm_forwarded_in_order():
    fake = FakeA2F().start()

    async def run():
        chunks = [b"\x01\x02", b"\x03\x04", b"\x05\x06"]
        fork = a2f_fork.A2FFork(fake.url, "happy")
        for c in chunks:
            fork.feed(c)
        fork.end()
        await fork.close()
        return chunks

    try:
        chunks = asyncio.run(run())
        assert _wait_for(lambda: any(e == ("end", True) for e in _peek(fake)))
        events = fake.drain_events()
    finally:
        fake.stop()

    # emotion first, then the exact 3 PCM blobs in order, then end.
    assert events[0] == ("emotion", "happy")
    pcm = [v for (tag, v) in events if tag == "pcm"]
    assert pcm == chunks
    assert ("end", True) in events
    # ordering: emotion before all pcm before end
    tags = [t for (t, _) in events]
    assert tags.index("emotion") < tags.index("pcm") < tags.index("end")


def test_emotion_list_forwarded_verbatim():
    fake = FakeA2F().start()
    emotion = [0.0] * 10

    async def run():
        fork = a2f_fork.A2FFork(fake.url, emotion)
        fork.feed(b"\x00\x00")
        fork.end()
        await fork.close()

    try:
        asyncio.run(run())
        assert _wait_for(lambda: any(e[0] == "end" for e in _peek(fake)))
        events = fake.drain_events()
    finally:
        fake.stop()

    assert ("emotion", emotion) in events


def test_close_without_end_sends_no_end():
    fake = FakeA2F().start()

    async def run():
        fork = a2f_fork.A2FFork(fake.url, None)
        fork.feed(b"\x01\x02")
        fork.feed(b"\x03\x04")
        # Wait until the fork has actually connected and streamed audio, so this
        # models a mid-utterance barge-in rather than a torn-down handshake.
        for _ in range(250):
            if any(e[0] == "pcm" for e in _peek(fake)):
                break
            await asyncio.sleep(0.02)
        # Barge-in: tear down WITHOUT signalling end-of-utterance.
        await fork.close()

    try:
        asyncio.run(run())
        assert _wait_for(lambda: any(e[0] == "closed" for e in _peek(fake)))
        events = fake.drain_events()
    finally:
        fake.stop()

    assert ("closed", None) in events
    assert all(tag != "end" for (tag, _) in events)


def test_dead_port_fails_gracefully():
    async def run():
        # Nothing listens on port 1 → connect fails.
        fork = a2f_fork.A2FFork("ws://127.0.0.1:1/a2f", None)
        # None of these may raise.
        fork.feed(b"\x01\x02")
        fork.end()
        await fork.close()
        return fork

    fork = asyncio.run(run())
    assert fork._failed is True


def _peek(fake):
    """Non-destructive snapshot of the fake's queued events."""
    return list(fake.events.queue)
