# Epic 8 Phase 1 remainder: TTS→A2F PCM fork + `emotion` on `/tts` — implementation plan

**Task:** https://github.com/prineycom/voice-agent/issues/33 (Epic 8, Phase 1 remainder)
**Complexity:** medium
**Mode:** sub-agents
**Parallel:** false

## Design decisions

### DD-1: Fork architecture — passive sink teed at the drain loop

**Decision:** A small `A2FFork` object (new module `infra/desktop/tts/a2f_fork.py`) per utterance owns an `asyncio.Queue`, a background `asyncio.Task`, and the A2F client WS. In the existing client drain loop (`server.py:146-154`), after `await ws.send_bytes(item)` add a fire-and-forget `fork.feed(item)` (`put_nowait`); on the completed sentinel call `fork.end()`.
**Rationale:** The client PCM path (`server.py:144-162`) stays authoritative and byte-for-byte unchanged except two additive non-blocking calls; all A2F concerns live in `a2f_fork.py`. `feed()` is `put_nowait` so a slow/blocked A2F never backpressures `ws.send_bytes`.
**Alternative:** Wrap `synthesize.stream_pcm` in an async tee — rejected: entangles the producer thread with the A2F client and muddies cancel.

### DD-2: A2F client lifecycle — per-utterance connect (not a reused socket)

**Decision:** Each `/tts` text message = one A2F utterance = one connect→emotion→PCM→`{"end"}`→drain→close.
**Rationale:** In Phase 1 frames are discarded, so reconnect latency is off the client critical path. Per-utterance connect makes cancel trivial (close the socket), removes the socket-reuse interleaving hazard, and matches the agent's per-sentence pipelining (one `{"done"}` per sentence, `tts_plugin.py:246`; one utterance per A2F outer-loop pass, `a2f/server.py:83`). Connect cost is loopback.
**Alternative:** One reused socket per `/tts` connection — deferred to Phase 3 (needs frame consumers + careful cancel); `A2FFork` is abstracted so only it changes then, not `server.py`.

### DD-3: Failure isolation — swallow everything, log-only, never touch the client

**Decision:** The fork task body catches ANY exception (connect refused / A2F down / slow / inference error / `{"error"}` frame), logs at WARNING, sets `self._failed`; `feed()`/`end()` become no-ops once failed. The server never awaits the fork on the normal path and never lets fork state gate the client. `/health` unchanged.
**Rationale:** The `/tts` client stream + barge-in are the contract; the fork is best-effort telemetry. A2F-down must be indistinguishable to the agent from a normal TTS stream (test case 6).
**Alternative:** Surface fork errors on `/health` — rejected for Phase 1; the fork is not a TTS dependency.

### DD-4: Emotion pass-through — forward verbatim, lenient parse, never reject

**Decision:** `server.py`: `emotion = req.get("emotion")`; if `not isinstance(emotion, (str, list))` → `None`. Absent/None ⇒ neutral (fork omits the `{"emotion":...}` message; A2F defaults neutral, `a2f/server.py:44-45,96-97`). Forward the value verbatim (string enum or float list) to A2F, which maps it (`a2f/server.py:44-51`, `emotion.py:28-30`).
**Rationale:** Emotion is NOT a synthesis argument (confirmed: no engine `stream_pcm` takes it, `engines.py:42-46`). Forwarding verbatim avoids duplicating `emotion.py` in TTS. A bad emotion must never error the `/tts` client — worst case A2F emits `{"error"}`, discarded by the fork.
**Alternative:** Map enum→vector in TTS — rejected: duplicates A2F logic, adds a dep on `emotion.py`.

### DD-5: Config — two env vars, read at request time

**Decision:** Add `A2F_WS_URL` (default `ws://127.0.0.1:8003/a2f`, loopback — TTS↔A2F co-located) and `A2F_FORK_ENABLED` (default `1`, ops kill-switch) to `.env.example`; read via `os.getenv` inside `tts_ws` per utterance. If disabled or URL empty, skip fork creation.
**Rationale:** Mirrors the agent's `*_WS_URL` convention (`agent/config.py:106-114`). Per-request read lets tests `monkeypatch.setenv` without reload. Loopback ⇒ no firewall/NSSM change.
**Alternative:** Module-level constant — rejected: harder to test, no runtime kill-switch.

### DD-6: Cancel / backpressure

**Decision:** Graceful end: `fork.end()` enqueues an end marker; the task sends `{"end"}` and drains frames to `{"done"}` in the background (not awaited on the request path). Forks are tracked in a connection-scoped list; an outer `finally` reaps them on socket close (`close()` idempotent). Barge-in: on client disconnect `ws.send_bytes` raises → per-utterance `finally` runs with `completed is False` → `if fork and not completed: await fork.close()`, which cancels the task and closes the A2F socket (A2F's receive loop then gets `websocket.disconnect`, `a2f/server.py:90-91`). The fork drains+discards every frame until `{"done"}`/`{"error"}` (backpressure constraint).
**Rationale:** Per-utterance socket (DD-2) makes teardown a simple close; nothing accumulates on cancel.
**Alternative:** Await graceful drain on the request path — rejected: adds client latency.

### DD-7: Tests — threaded fake `/a2f` + `asyncio.run` units + `TestClient` integration

**Decision:** `tests/fake_a2f.py`: threaded `websockets.serve` on `127.0.0.1:0`, records emotion/PCM/end into a `queue.Queue`, replies 1–2 `{"type":"blendshapes"}` frames + `{"done":true}`. `test_a2f_fork.py` (unit, `asyncio.run`): (1) emotion-str+PCM+end ordering & audio-binding, (2) emotion-list verbatim, (3) cancel → `ConnectionClosed` without `{"end"}`, (4) dead port → `_failed`, no raises. `test_server_fork.py` (integration, `TestClient`): (5) `{"text","voice","emotion":"happy"}` → client gets all PCM + `{"done"}` AND fake shows emotion+same PCM+end, (6) A2F down → client still gets all PCM + `{"done"}`.
**Rationale:** GPU-free (no CUDA, no pytest-asyncio dep — `websockets` already at `requirements.txt:17`); mirrors `a2f/tests/test_server.py:35-53` ordering and `tts/tests/test_server.py:15-28,65-73` fixtures.

### DD-8: Scope discipline — Phase 3 follow-ups NOT here

**Decision:** No agent changes. Agent SENDING `emotion` on `/tts` (`tts_plugin.py:158,246`) and agent CONSUMING blendshape frames (fan-out → data channel) are Phase 3 — flagged as paired follow-ups. Phase 1 drains/discards frames.

## Tasks

### Task 1: `A2FFork` module

- **Files:** `infra/desktop/tts/a2f_fork.py` (create)
- **Depends on:** none
- **Scope:** M (~90 LOC)
- **What:** `class A2FFork(url, emotion)` + `maybe_start_fork(emotion) -> A2FFork | None`, per DD-1/2/3/4/5/6.
- **How:** `websockets.connect(url, max_size=None)` client (mirror `tts_plugin.py:30,156`); internal `asyncio.Queue`; `self._task = asyncio.create_task(self._run())`. `_run`: connect → send `{"emotion":...}` once if set → stream PCM chunks → `{"end":true}` → drain+discard frames until `{"done"}`/`{"error"}`. Swallow ALL exceptions → `self._failed`; log WARNING. `feed(pcm)` = `put_nowait`, no-op if failed/ended. `end()` = enqueue end marker. `close()` = graceful (bounded wait) if `_ended` else cancel; idempotent. `maybe_start_fork` reads `A2F_WS_URL`/`A2F_FORK_ENABLED`, returns None if disabled/empty.
- **Context:** wire order `a2f/server.py:88-104`; client style `tts_plugin.py:30,156-160`; env DD-5.
- **Verify:** covered by Task 4/5 (`pytest tests/test_a2f_fork.py`).

### Task 2: Wire the fork into `server.py`

- **Files:** `infra/desktop/tts/server.py` (edit)
- **Depends on:** Task 1
- **Scope:** S (~12 lines)
- **What:** Parse `emotion` (DD-4); create/feed/end/close the fork alongside the untouched client drain loop; reap forks on socket close.
- **How:** after `voice = req.get("voice","default")` (`:121`) parse emotion; add `forks: list = []` at connection scope (before `while True`, `:113`); `fork = a2f_fork.maybe_start_fork(emotion)` in a guarded try (→ None on error), append to `forks`; in the drain loop after `await ws.send_bytes(item)` (`:154`) `if fork: fork.feed(item)`; on completed (`:149`) `if fork: fork.end()`; per-utterance `finally` (`:160-162`) add `if fork and not completed: await fork.close()`; outer `finally`/disconnect (`:163`) `for f in forks: await f.close()`.
- **Context:** `server.py:105-170`; keep the client loop intact.
- **Verify:** `cd infra/desktop/tts && python -m pytest tests/test_server.py` — still green (no regression).

### Task 3: `.env.example` config

- **Files:** `infra/desktop/tts/.env.example` (edit)
- **Depends on:** none
- **Scope:** S (~5 lines)
- **What:** add `A2F_WS_URL=ws://127.0.0.1:8003/a2f` and `A2F_FORK_ENABLED=1` with a one-line comment (fork tees PCM+emotion to A2F; frames discarded in Phase 1).
- **Context:** existing `.env.example` style.
- **Verify:** grep shows both keys.

### Task 4: `tests/fake_a2f.py` helper

- **Files:** `infra/desktop/tts/tests/fake_a2f.py` (create)
- **Depends on:** none
- **Scope:** M (~50 LOC)
- **What:** threaded fake `/a2f` WS server recording the exact client message order, replying frames + `{"done"}`.
- **How:** `websockets.serve` on `127.0.0.1:0` in a background thread with its own loop; handler reads emotion/PCM/`{"end"}` (mirror `a2f/tests/test_server.py:35-40`), records into a thread-safe `queue.Queue`, replies 1–2 `{"type":"blendshapes"}` frames + `{"done":true}`, records `ConnectionClosed`. Expose bound `url` + recorder; context-manager teardown.
- **Context:** `a2f/tests/test_server.py:35-53`.
- **Verify:** imported + smoke-connected by Task 5.

### Task 5: `test_a2f_fork.py` (unit)

- **Files:** `infra/desktop/tts/tests/test_a2f_fork.py` (create)
- **Depends on:** Task 1, Task 4
- **Scope:** M (~90 LOC)
- **What:** DD-7 cases 1–4 via `asyncio.run`, driving `A2FFork` directly.
- **Context:** `tests/fake_a2f.py`, final `a2f_fork.py`.
- **Verify:** `cd infra/desktop/tts && python -m pytest tests/test_a2f_fork.py`.

### Task 6: `test_server_fork.py` (integration)

- **Files:** `infra/desktop/tts/tests/test_server_fork.py` (create)
- **Depends on:** Task 2, Task 4
- **Scope:** M (~70 LOC)
- **What:** DD-7 cases 5–6 via `TestClient` + `websocket_connect`, `monkeypatch.setenv A2F_WS_URL`, monkeypatched `synthesize.stream_pcm`.
- **Context:** `tts/tests/test_server.py:15-28,65-85`.
- **Verify:** `cd infra/desktop/tts && python -m pytest tests/test_server_fork.py`.

### Task 7: Validation

- **Files:** —
- **Depends on:** all
- **Scope:** S
- **What:** Full suite green + scope discipline.
- **How:** from `infra/desktop/tts/`, `python -m pytest tests/ -q 2>&1 | tail -20` (incl. pre-existing `test_server.py`, `test_synthesize.py`, `test_audio.py`); `git diff --stat` confirms NO changes to agent code, `a2f/`, `requirements.txt`, `synthesize.py`, `engines.py`.
- **Verify:** green suite + clean scope diff.

## Execution

- **Mode:** sub-agents
- **Parallel:** false
- **Reasoning:** 6 small, tightly-coupled files in one service dir (~420 LOC); the `a2f_fork.py`↔`server.py` cancel/drain invariants benefit from one implementer holding them; file-level parallelism doesn't justify multi-agent coordination at this size.
- **Order:** Task 1 → Task 3 → Task 4 → Task 2 → Task 5 → Task 6 → Task 7

## Verification

- `/tts` accepts an `emotion` field (string enum or 10-float list; absent ⇒ neutral) and never rejects a bad value.
- The TTS service forks each utterance's PCM + emotion into the A2F `/a2f` WS in the correct order (emotion → PCM → `{"end"}`) and drains/discards the returned blendshape frames.
- A2F down/slow/erroring never affects the `/tts` client stream or barge-in (proven by test).
- Barge-in tears down the fork socket; no interleave, no orphan drain.
- Full GPU-free test suite green; no changes to agent code, the `a2f/` service, `requirements.txt`, or the synthesis path.

## Materials

- Files: `infra/desktop/tts/{server.py,synthesize.py,engines.py,.env.example}`, `infra/desktop/tts/tests/{test_server.py,conftest.py}`, `infra/desktop/a2f/{server.py,emotion.py}`, `infra/desktop/a2f/tests/test_server.py`, `infra/pi/agent/{tts_plugin.py,config.py}`.
- Epic: https://github.com/prineycom/voice-agent/issues/33 (decisions #3, #5, #6).
- Follow-ups (Phase 3, NOT here): agent sends `emotion` on `/tts`; agent consumes/forwards blendshape frames to the `voiceagent` data channel.
