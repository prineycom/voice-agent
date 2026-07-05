# Epic 8 Phase 3: Agent + LiveKit integration — implementation plan

**Task:** GitHub issue #36 (https://github.com/prineycom/voice-agent/issues/36)
**Complexity:** complex
**Mode:** sub-agents
**Parallel:** true

Close the loop so live TTS audio drives the Live2D face end-to-end. The frontend
end of the wire (`type:"blendshapes"` on `voiceagent`, `{done:true}`, per-frame
`t`) is ALREADY pinned by #35 — Phase 3 must produce exactly that.

## Pinned cross-service wire contract (FREEZE before parallel work)

Both lanes implement these byte-for-byte identically:

- **Desktop→Agent on `/tts` (new):** `{"type":"blendshapes","frame":<int>,"t":<float, sentence-relative from 0>,"arkit":{...}}` — forwarded **verbatim** by the fork (only the agent re-bases `t`).
- **Desktop→Agent on `/tts` (new):** `{"type":"a2f_done"}` — exactly **one per sentence**, agent-internal, never forwarded to the browser.
- **Desktop→Agent on `/tts` (unchanged):** `{"done":true}` — per-sentence audio end; agent-internal (`seen_done`), never forwarded.
- **Agent→browser on `voiceagent` (`reliable=False`, matches #35):** per-frame `{"type":"blendshapes","frame","t":<reply-relative>,"arkit":{...}}`; then **one** `{"done":true}` per reply.

## Design decisions

### DD-1: Fork forwards frames via an injected callback; keeps A2F feeding on loopback
**Decision:** `A2FFork.__init__(url, emotion, on_frame=None, on_done=None)`. `_run` calls `on_frame(frame_dict)` for each `{"type":"blendshapes",...}` instead of discarding (`a2f_fork.py:110-115`), preserving `frame`/`t`/`arkit` verbatim.
**Rationale:** The fork is the only object that owns the A2F recv loop; a callback keeps it decoupled/unit-testable and lets `server.py` route frames into its outbound queue (the fork has no client-socket handle today — `a2f_fork.py:35`).
**Alternative:** Move the A2F recv into the `/tts` handler — rejected: duplicates connection lifecycle already encapsulated in `A2FFork` and breaks its self-contained teardown (`a2f_fork.py:77-96`).

### DD-2: Single-writer outbound queue on the `/tts` handler; PCM reliable, blendshapes drop-on-full
**Decision:** One bounded `asyncio.Queue` per connection, drained by one writer task. Items tagged `("bin", bytes)` / `("json", dict)`. PCM and audio-`{done}` use `await put` (natural audio backpressure, never dropped). Blendshapes use `put_nowait` and drop on `QueueFull`. Replaces inline `ws.send_bytes` (`server.py:178`).
**Rationale:** Two concurrent producers (PCM drain loop + fork frame callback) on one WebSocket is unsafe without a single writer; drop-on-full guarantees A2F backpressure can never stall audio.
**Alternative:** A lock around `ws.send_*` — rejected: a slow client would let blendshape sends serialize *ahead of* PCM and add audio latency; a bounded queue + drop is the specified model (DD-6 of the issue).

### DD-3: A2F's own `{done}` is suppressed and relabeled to a per-sentence `a2f_done` marker
**Decision:** The fork does **not** forward A2F's `{done}` (`a2f_fork.py:114`). On fork completion (`finally`, so success/failure/cancel all covered) it emits **exactly one** `{"type":"a2f_done"}`. When no fork is started (A2F disabled/unconfigured), the `/tts` handler emits the marker itself after the sentence's audio `{done}` — guaranteeing exactly `total_sends` markers in every configuration.
**Rationale:** Prevents collision with the per-sentence audio `{done}` the plugin counts, AND gives the agent a reliable per-sentence "blendshapes complete" signal needed for DD-4. `finally`-emission + handler-fallback preserves failure isolation (A2F down → fork fails fast → marker still emitted → agent never blocks).
**Alternative:** Pure-suppress with no marker — rejected (see DD-4): leaves trailing frames unread on the persistent socket → cross-reply pollution and mis-based `t`.

### DD-4: Agent gates `_recv` exit on both audio-done and a2f-done counts; emits one reply-`{done}`
**Decision:** Exit when `send_done and seen_done >= total_sends and seen_a2f_done >= total_sends`. Emit a single `voiceagent {"done":true}` once per reply (guarded `_reply_done_sent`) on normal completion and on barge-in/error. Counters reset at `_run` entry (reply start) and on the cancel path.
**Rationale:** One `_run` invocation ≈ one reply (serialized by `self._lock`, `tts_plugin.py:210`); waiting for a2f-done drains the last sentence's frames so they don't pollute the next reply and the full last-sentence face is forwarded. Because audio playout of the final sentence outlasts A2F lag, `_run` returning slightly later adds **no** user-perceived latency.
**Alternative:** Exit on audio-done only + drop/reconnect the socket every reply — simpler, but truncates the last sentence's face (backstopped only by the 250 ms idle timeout, `blendshapes.js:22`).

### DD-5: Reply-relative `t` via a running offset advanced on `a2f_done`
**Decision:** Track per-sentence audio duration (bytes pushed / 2 / `SAMPLE_RATE`, finalized on each audio `{done}`). Maintain `reply_offset_s`; on the k-th `a2f_done` add that sentence's duration. Each blendshape frame is forwarded with `t' = frame["t"] + reply_offset_s` (current). This re-bases sentence-N frames by Σ prior-sentence durations. Reset at reply start and barge-in.
**Rationale:** Faithful to the requirement (offset = Σ prior-sentence audio durations); advancing on the frame-stream boundary (`a2f_done`) rather than the audio `{done}` keeps `t` monotonic under the real ordering (frames arrive *after* their audio `{done}`).
**Alternative:** Advance on audio `{done}` — rejected: frames arriving after their own `{done}` would overshoot into the next sentence's range, breaking monotonicity.

### DD-6: Emotion attached at sentence-flush time via an injected source
**Decision:** `DesktopTTS.set_emotion_source(getter)`; `_send` reads `normalize_emotion(getter())` (default `neutral`) and adds `"emotion"` to each request JSON (`tts_plugin.py:247`). The getter is `lambda: agent.current_emotion`.
**Rationale:** `current_emotion` is updated in `llm_node` upstream of `_send` (`agent.py:114-122`); reading at flush time gives "latest tag before the sentence flush wins." `/tts` already accepts `emotion` (`server.py:125`); A2F normalizes.
**Alternative:** Push emotion into the plugin on each tag — rejected: `_send` is decoupled from `llm_node`; a pull-at-flush read is race-free enough for a best-effort field.

### DD-7: Reuse `publish_motion` as the blendshape/done sink
**Decision:** `DesktopTTS.set_publisher(publish_motion)`; the plugin calls `self._publish(json.dumps(...).encode())` for frames and the reply-`{done}`.
**Rationale:** `publish_motion` already encodes the lossy `reliable=False` `UI_TOPIC` publish with error-swallowing (`agent.py:251-263`) — no new publisher, no new head-of-line-blocking risk (#23).
**Alternative:** A dedicated async publisher awaited in `_recv` — rejected: awaiting per-frame risks coupling recv throughput to SFU send; the sync fire-and-forget matches the motion path.

### DD-8: Barge-in emits the single reply-`{done}` best-effort
**Decision:** In the `_run` cancel/`APIError`/`Exception` handlers (`tts_plugin.py:322-332`), call the guarded reply-done emit before/with `_drop_ws`. Desktop socket close → forks close (`server.py` finally) → A2F stops; frontend 250 ms idle timeout is the backstop.
**Rationale:** Matches the "close socket = stop" contract; `{done}` on both paths keeps the guard idempotent.

## Tasks

### Task D1: Fork — forward frames + emit relabeled marker
- **Files:** `infra/desktop/tts/a2f_fork.py` (edit), `infra/desktop/tts/tests/test_a2f_fork.py` (edit)
- **Depends on:** none (contract §top)
- **Scope:** M
- **What:** Replace drain-and-discard (`a2f_fork.py:110-115`) with `on_frame(frame)` forwarding; suppress A2F `{done}`; emit `on_done()` once in a `finally`. Add `on_frame`/`on_done` params to `__init__` and `maybe_start_fork`.
- **How:** Parse each A2F text msg; if `type=="blendshapes"` call `on_frame(data)`; on `{done}`/error break. Wrap the `_run` body so `finally: on_done()` fires on drain, failure, and cancel (guard against double-fire). Keep `feed`/`end`/`close` untouched.
- **Context:** `infra/desktop/tts/a2f_fork.py:35-131`, `infra/desktop/tts/tests/test_a2f_fork.py`, `infra/desktop/tts/tests/fake_a2f.py:62-74` (already sends 2 frames + done).
- **Verify** (manual/Desktop-run): `cd infra/desktop/tts && python -m pytest tests/test_a2f_fork.py` — new assertions: both blendshape frames delivered to an `on_frame` spy in order; `on_done` called exactly once on normal end, on dead-port failure, and on close-without-end.

### Task D2: `/tts` single-writer queue + fork wiring
- **Files:** `infra/desktop/tts/server.py` (edit), `infra/desktop/tts/tests/test_server_fork.py` (edit), `infra/desktop/tts/tests/fake_a2f.py` (edit if needed)
- **Depends on:** Task D1 (new `maybe_start_fork` signature)
- **Scope:** L
- **What:** Introduce a per-connection bounded `out_queue` + one writer task (`("bin",bytes)`→`send_bytes`, `("json",dict)`→`send_json`, sentinel to stop). Route PCM (`await put`) and per-sentence `{done}` (`await put`) through it; wire fork `on_frame`→`put_nowait` drop-on-`QueueFull`, fork `on_done`→`await put({"type":"a2f_done"})`; when no fork, the handler emits the marker after the audio `{done}`. Preserve producer-cancel + fork-close teardown and client-disconnect isolation.
- **How:** Start the writer before the sentence loop; stop it (sentinel + await) in the outer `finally` alongside `for f in forks: await f.close()`. Keep `state["loaded"]` gate and error frames intact.
- **Context:** `infra/desktop/tts/server.py:106-201` (PCM send loop + fork lifecycle), `infra/desktop/tts/tests/test_server_fork.py:36-83`.
- **Verify** (manual/Desktop-run): `cd infra/desktop/tts && python -m pytest tests/test_server_fork.py tests/test_server.py` — `_collect_until_done` (:36) tolerates interleaved `blendshapes`/`a2f_done` and still asserts the terminal `{done}`; PCM bit-exact and unaffected when A2F is down.

### Task A1: Plugin — demux, re-base, emotion, injection, reply-done (+ tests)
- **Files:** `infra/pi/agent/tts_plugin.py` (edit), `infra/pi/agent/tests/conftest.py` (edit), `infra/pi/agent/tests/test_tts_plugin.py` (edit)
- **Depends on:** none (contract §top; parallel to Desktop lane)
- **Scope:** L
- **What:** (a) `DesktopTTS`: add `_publish`/`_emotion_source` + `set_publisher`/`set_emotion_source`. (b) `_send`: attach `normalize_emotion(self._emotion_source())` (default `neutral`) to request JSON. (c) `_recv`: demux blendshapes (re-base `t` per DD-5, forward via `_publish`), `a2f_done` (`seen_a2f_done`, advance `reply_offset_s`), audio `{done}` (`seen_done`, finalize sentence duration); extend exit condition. (d) Track per-sentence pushed-byte duration. (e) Emit one guarded `voiceagent {done}` on normal end and in the cancel/`APIError`/`Exception` handlers. (f) Import `normalize_emotion` from `motion_events`.
- **How:** Reuse the `send_done`/`total_sends` machinery, adding a parallel `seen_a2f_done`. `_publish`/`_emotion_source` default to no-op/`neutral` when unset (tests, fallback). Conftest: `FakeTTSServer` emits per sentence `[PCM][blendshapes...][{done}][{a2f_done}]`, records forwarded `voiceagent` publishes via an injected spy.
- **Context:** `infra/pi/agent/tts_plugin.py:205-333` (whole `_run`/`_send`/`_recv`), `:42` (SAMPLE_RATE), `:228-306` (rendezvous), `infra/pi/agent/motion_events.py:42-51` (`normalize_emotion`/`DEFAULT_EMOTION`), `infra/pi/agent/tests/conftest.py:35` (`FakeTTSServer`), `infra/pi/agent/tests/test_tts_plugin.py:66,276,329,431`.
- **Verify:** `cd infra/pi/agent && .venv/bin/python -m pytest tests/test_tts_plugin.py -q` — new tests: emotion threaded (latest-before-flush + default); `t` monotonic reply-relative across a 2-sentence reply; one `{done}` per reply; drop-on-full never blocks PCM; A2F-down leaves audio + barge-in unaffected; barge-in emits `{done}` and stops forwarding.

### Task A2: Entrypoint wiring
- **Files:** `infra/pi/agent/agent.py` (edit)
- **Depends on:** Task A1 (setters must exist)
- **Scope:** S
- **What:** Hoist `DesktopTTS(...)` (currently inline in `AgentSession(tts=...)`, `agent.py:300`) to `desktop_tts = DesktopTTS(...)`; pass `tts=desktop_tts`. After `agent = GreetingAgent(...)` (`:359`), call `desktop_tts.set_publisher(publish_motion)` and `desktop_tts.set_emotion_source(lambda: agent.current_emotion)`.
- **How:** `publish_motion` is already defined (`:251`) before the session block; `agent` exists only after construction, so inject post-construction. No behavior change to motion/state hooks.
- **Context:** `infra/pi/agent/agent.py:251-263` (`publish_motion`), `:285-364` (entrypoint session/agent construction), `:95` (`current_emotion`).
- **Verify:** `cd infra/pi/agent && .venv/bin/python -m pytest tests/ -q` — full suite green (import/wire smoke).

### Task V: Validation
- **Files:** —
- **Depends on:** all (D1, D2, A1, A2)
- **Scope:** M
- **What:** (1) Confirm the wire contract (top of file) is byte-identical across `a2f_fork.py`/`server.py` and `tts_plugin.py`. (2) Run the Agent suite on the Pi. (3) Flag the Desktop suite as **manual/Desktop-run** (cannot run on the Pi). (4) Manual e2e note: multi-sentence emotion-tagged reply → face drives from live TTS, one `voiceagent {done}` per reply, barge-in stops the face. (5) Measure-first sync gate: capture the `mouth.js` `crossCorrelateOffset` "mouth sync offset: N ms" log across several utterances and record the offset as a documented decision (no code).
- **Context:** —
- **Verify:** `cd infra/pi/agent && .venv/bin/python -m pytest tests/ -q` — green on Pi. Desktop suite green on Desktop (manual). E2E checklist + recorded sync-offset note.

## Execution

- **Mode:** sub-agents
- **Parallel:** true
- **Reasoning:** Desktop lane {D1,D2} and Agent lane {A1,A2} touch fully disjoint file sets and couple only through the frozen wire contract, so the two lanes run concurrently; V is last and depends on all.
- **Order:**
  Group 1 (parallel): Task D1, Task A1
  ─── barrier ───
  Group 2 (parallel): Task D2, Task A2
  ─── barrier ───
  Group 3 (sequential): Task V

## Verification

Definition of done (from the issue):
- Live TTS audio drives Hiyori's face end-to-end: A2F frames forwarded on `voiceagent` with `t` preserved; the frontend rig moves in time with speech.
- The parsed emotion reaches A2F via the `/tts` `emotion` field; facial emotion is A2F-only (no `.exp3.json`).
- Barge-in stops A2F and decays the face to neutral promptly.
- Mouth lead/lag measured; a documented decision to keep apply-on-arrival or enable the adaptive delay loop (or retreat mouth opening to `VolumeLipSync`).
- Failure isolation holds: A2F/transport down → audio + barge-in unaffected, mouth falls back to the volume provider, face neutral.
- Unit + integration tests green (emotion threading, PTS preservation, failure isolation, ordered forwarding, barge-in stop).

## Notes — Desktop vs Pi execution

Desktop TTS/A2F tests (`infra/desktop/tts/tests/`) run on the **Windows desktop**, not the Pi where this executor runs. Tasks D1/D2 Verify steps are therefore **manual/Desktop-run**; the Agent lane (A1/A2) and V must not block on them. Code changes for D1/D2 are made here on the Pi checkout; the Desktop pytest run is confirmed separately.
