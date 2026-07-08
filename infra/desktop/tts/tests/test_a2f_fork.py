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
    frames = []
    done_calls = []

    async def run():
        # Nothing listens on port 1 → connect fails.
        fork = a2f_fork.A2FFork(
            "ws://127.0.0.1:1/a2f",
            None,
            on_frame=frames.append,
            on_done=lambda: done_calls.append(1),
        )
        # None of these may raise.
        fork.feed(b"\x01\x02")
        fork.end()
        await fork.close()
        return fork

    fork = asyncio.run(run())
    assert fork._failed is True
    # No frames on failure; on_done still fires exactly once.
    assert frames == []
    assert done_calls == [1]


def test_blendshape_frames_forwarded_verbatim():
    fake = FakeA2F().start()
    frames = []

    async def run():
        fork = a2f_fork.A2FFork(fake.url, "happy", on_frame=frames.append)
        fork.feed(b"\x01\x02")
        fork.end()
        await fork.close()

    try:
        asyncio.run(run())
        assert _wait_for(lambda: len(frames) >= 2)
    finally:
        fake.stop()

    # Both blendshape frames delivered in order, dicts verbatim; done NOT surfaced.
    assert len(frames) == 2
    assert frames[0] == {
        "type": "blendshapes",
        "frame": 0,
        "t": 0.0,
        "arkit": {"JawOpen": 0.1, "MouthSmileLeft": 0.2},
    }
    assert frames[1] == {
        "type": "blendshapes",
        "frame": 1,
        "t": 1 / 30.0,
        "arkit": {"JawOpen": 0.1, "MouthSmileLeft": 0.2},
    }
    assert all("done" not in f for f in frames)


def test_frames_forward_while_still_feeding():
    """Interleaved send/recv: a frame A2F emits after the first PCM chunk must
    reach on_frame BEFORE end() is called on the fork — not queued until the
    whole utterance has been sent."""
    fake = FakeA2F(early_frames=1).start()
    frames = []

    async def run():
        got_frame = asyncio.Event()

        def on_frame(frame):
            frames.append(frame)
            got_frame.set()

        fork = a2f_fork.A2FFork(fake.url, "happy", on_frame=on_frame)
        fork.feed(b"\x01\x02")
        # The early frame must arrive while we are still feeding, i.e. before
        # end(). A send-all-then-recv fork would time out here.
        await asyncio.wait_for(got_frame.wait(), timeout=5.0)
        frames_before_end = len(frames)
        fork.feed(b"\x03\x04")
        fork.end()
        await fork.close()
        return frames_before_end

    try:
        frames_before_end = asyncio.run(run())
    finally:
        fake.stop()

    assert frames_before_end >= 1
    assert frames[0].get("early") is True
    # After a graceful drain the post-end burst (2 frames) arrived too;
    # done is still suppressed.
    assert len(frames) == 3
    assert all(f["type"] == "blendshapes" for f in frames)


def test_raising_on_frame_does_not_kill_drain_loop():
    fake = FakeA2F().start()
    received = []
    done_calls = []

    def spy(frame):
        received.append(frame)
        # Raise on the FIRST frame only; subsequent frames must still forward.
        if len(received) == 1:
            raise RuntimeError("boom in consumer")

    async def run():
        fork = a2f_fork.A2FFork(
            fake.url,
            "happy",
            on_frame=spy,
            on_done=lambda: done_calls.append(1),
        )
        fork.feed(b"\x01\x02")
        fork.end()
        await fork.close()
        return fork

    try:
        fork = asyncio.run(run())
        assert _wait_for(lambda: len(received) >= 2)
    finally:
        fake.stop()

    # A raising on_frame must NOT mark the fork failed nor drop remaining frames.
    assert fork._failed is False
    # Both frames reached the consumer (the raise on frame 0 did not abort frame 1).
    assert len(received) == 2
    # on_done still fires exactly once after a graceful drain.
    assert done_calls == [1]


def test_on_done_fires_once_on_normal_end():
    fake = FakeA2F().start()
    done_calls = []

    async def run():
        fork = a2f_fork.A2FFork(
            fake.url, None, on_done=lambda: done_calls.append(1)
        )
        fork.feed(b"\x01\x02")
        fork.end()
        await fork.close()

    try:
        asyncio.run(run())
        assert _wait_for(lambda: done_calls == [1])
    finally:
        fake.stop()

    assert done_calls == [1]


def test_on_done_fires_once_on_close_without_end():
    fake = FakeA2F().start()
    done_calls = []

    async def run():
        fork = a2f_fork.A2FFork(
            fake.url, None, on_done=lambda: done_calls.append(1)
        )
        fork.feed(b"\x01\x02")
        # Wait until the fork has connected and streamed audio, modelling a
        # mid-utterance barge-in.
        for _ in range(250):
            if any(e[0] == "pcm" for e in _peek(fake)):
                break
            await asyncio.sleep(0.02)
        # Barge-in: cancel WITHOUT signalling end-of-utterance.
        await fork.close()

    try:
        asyncio.run(run())
    finally:
        fake.stop()

    # on_done fires exactly once even on cancel.
    assert done_calls == [1]


def _peek(fake):
    """Non-destructive snapshot of the fake's queued events."""
    return list(fake.events.queue)
