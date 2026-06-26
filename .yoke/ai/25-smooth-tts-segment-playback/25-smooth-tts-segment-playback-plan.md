# Choppy agent speech — smooth playback across TTS segments — implementation plan

**Task:** https://github.com/prineycom/voice-agent/issues/25
**Complexity:** medium
**Mode:** sub-agents
**Parallel:** true

## Design decisions

### DD-1: Full streaming `SynthesizeStream` over a persistent socket with eager sentence-pipelined sends

**Decision:** Implement a `streaming=True` path on `DesktopTTS`: one persistent WebSocket reused per session, a blingfire `SentenceTokenizer`, and a `DesktopSynthesizeStream._run` that sends each sentence's JSON over the open socket eagerly and drains PCM in a concurrent reader.
**Rationale:** The root-cause gap is `socket-connect + Qwen3-ttfb` per sentence — `StreamAdapterWrapper._run` (`livekit/agents/tts/stream_adapter.py:120-136`) opens a fresh socket and serially drains each sentence. A persistent socket removes the connect cost; eager pipelined sends let the server read sentence N+1 the instant it finishes N. The reader drains PCM faster than realtime into the framework's `AudioEmitter` buffer, so the server synthesizes N+1 while the client still plays N — hiding inter-sentence latency behind playback. The Desktop `/tts` server already loops over multiple messages per connection (`infra/desktop/tts/server.py:72`), so this needs ZERO server changes.
**Alternative:** (b) per-segment sockets with pre-buffer — still pays a fresh `websockets.connect` per sentence, strictly worse with no simplification. (c) tuning-only (`SentenceStreamPacer` / larger `TTS_CHUNK_SIZE`) — only merges fragments, leaves the connect+ttfb gap intact, fails "measurably reduced gaps" on short sentences.

### DD-2: Sentence-pipeline, not single whole-response message

**Decision:** Tokenize the LLM token stream into sentences (blingfire) and send each as it completes.
**Rationale:** In streaming mode the framework hands raw LLM tokens with one terminal `end_input` and no per-sentence flush (`livekit/agents/voice/agent.py:515-519`). Sending the whole response as one `/tts` message would be gap-free but regress first-audio-latency badly (wait for full LLM completion before any audio). Sentence pipelining preserves "start speaking while the LLM still generates."
**Alternative:** Single whole-response message — rejected for first-audio-latency regression.

### DD-3: Resolve barge-in vs persistence with drop-on-cancel (preserve ADR-0006)

**Decision:** On barge-in catch `asyncio.CancelledError`, call `_drop_ws()` (close + forget), re-raise — mirroring `infra/pi/agent/stt_plugin.py:148-156`. Next turn's `_ensure_ws()` reconnects fresh.
**Rationale:** Closing the socket triggers the server's existing disconnect→`cancel.set()` producer abort (`infra/desktop/tts/server.py:120`); eagerly-queued sentences die with the socket (no stale next-turn audio). This keeps the ADR-0006 contract ("interruption = close the TTS WebSocket; no in-band stop") intact while the socket persists across non-interrupted turns.
**Alternative:** Add an in-band `/tts` cancel message — ADR-0006 explicitly rejects it; would require a server protocol change.

### DD-4: One `start_segment` + per-sentence `flush()`; do not override `_metrics_monitor_task`

**Decision:** Call `start_segment` exactly once and `flush()` to demarcate each sentence, mirroring `StreamAdapterWrapper` (`stream_adapter.py:107,137`). Leave the base metrics task active.
**Rationale:** The framework pushes one input segment per stream; `_main_task` asserts `self._num_segments == output_emitter.num_segments` (`livekit/agents/tts/tts.py:492`), and `start_segment` increments `num_segments`. Keeping the base `_metrics_monitor_task` means `TTSMetrics`/`ttfb` still fires, so `agent.py:223-229` (`_on_metrics`) and the `TTS ttfb` log need NO change.
**Alternative:** Custom metrics — unnecessary, risks breaking the assertion.

### DD-5: Additive, flag-guarded rollout; keep `synthesize()`/`ChunkedStream` unchanged

**Decision:** Add `tts_streaming` config (default `True`) wired into `DesktopTTS(streaming=...)`. Leave the existing one-shot `synthesize()`/`ChunkedStream` path (`tts_plugin.py:49-107`) untouched as a fallback.
**Rationale:** Operators can fall back to the proven `StreamAdapter` path (`streaming=False`) without a code change if the GPU can't synthesize faster-than-realtime. The unchanged one-shot path keeps existing tests a live regression net and `say()`/non-streaming callers working.
**Alternative:** Hard replace — loses the fallback and invalidates existing tests.

**True-overlap caveat (scope boundary):** Real concurrent synthesis of N+1 *during* N's synthesis (not just N's playback) would require the server to run producers concurrently — the `/tts` loop is serial per message (`server.py:103-121`). Out of scope; the playback-buffer overlap is what hides the gap without touching the server. If synthesis is slower than realtime, residual gaps are a hardware limit, not a client fix.

## Tasks

### Task 1: Add `tts_streaming` config flag

- **Files:** `infra/pi/agent/config.py`, `infra/pi/agent/tests/test_config.py`
- **Depends on:** none
- **Scope:** S
- **What:** Add `tts_streaming: bool` to `AgentConfig` and parse `TTS_STREAMING` env (default `True`).
- **How:** Add the field after `config.py:27`. In `load_config` (near lines 95-100) parse `os.environ.get("TTS_STREAMING", "true")` to bool (truthy set `{"1","true","yes","on"}`, case-insensitive), following the existing bool/int parse style at `config.py:61-83`. Add it to the `AgentConfig(...)` constructor. Add a `test_config.py` case asserting default `True` and that `TTS_STREAMING=false` yields `False`.
- **Context:** `config.py:15-43,61-101`; `tests/test_config.py`.
- **Verify:** From `infra/pi/agent`: `.venv/bin/python -m pytest tests/test_config.py -q` passes.

### Task 2: Implement the streaming TTS path in `tts_plugin.py`

- **Files:** `infra/pi/agent/tts_plugin.py`
- **Depends on:** none
- **Scope:** L
- **What:** Add a `streaming` constructor toggle, persistent-socket lifecycle, blingfire tokenizer, `stream()`, and `DesktopSynthesizeStream._run` that pipelines sentence sends over one socket and drains PCM in order, with drop-on-cancel barge-in. Leave `synthesize()`/`ChunkedStream` (lines 49-107) untouched.
- **How:**
  - `DesktopTTS.__init__(..., streaming: bool = True)` → `capabilities=tts.TTSCapabilities(streaming=streaming)`. Add `self._lock = asyncio.Lock()`, `self._ws = None`, `self._tokenizer` (a `livekit.agents.tokenize` blingfire `SentenceTokenizer`, the same class `StreamAdapter` uses at `stream_adapter.py:39`).
  - Add `_ensure_ws`/`_drop_ws`/`aclose` mirroring `stt_plugin.py:55-85,171-177`.
  - `def stream(self, *, conn_options=DEFAULT_API_CONNECT_OPTIONS) -> DesktopSynthesizeStream`.
  - `DesktopSynthesizeStream(tts.SynthesizeStream)._run(output_emitter)`: acquire `self._tts._lock`; `_ensure_ws()`; `output_emitter.initialize(request_id, sample_rate, NUM_CHANNELS, MIME_TYPE, stream=True)`; one `start_segment(segment_id=...)`. Build `sent_stream = self._tts._tokenizer.stream()`. Run three coroutines under `gather`, with `finally: sent_stream.aclose()` + cancel pending tasks:
    1. `_feed`: `async for data in self._input_ch:` → `_FlushSentinel`→`sent_stream.flush()` else `sent_stream.push_text(data)`; then `sent_stream.end_input()`.
    2. `_send`: `async for ev in sent_stream:` strip; if non-empty `await ws.send(json.dumps({"text": text, "voice": voice}))`, count sends; on completion record the final total.
    3. `_recv`: loop `await ws.recv()`; bytes → 16-bit leftover-carry align (reuse `tts_plugin.py:86-96`) → `output_emitter.push`; `{"error"}`→`raise APIError`; `{"done":true}`→`output_emitter.flush()` and increment seen-done; exit when all sends are done and seen-done ≥ total. Guard the empty-response (total==0) case before awaiting recv.
  - `except asyncio.CancelledError: await self._tts._drop_ws(); raise`. On `APIError`/other → `_drop_ws()` then raise (a half-drained persistent socket must not leak into the next turn).
  - Do NOT override `_metrics_monitor_task` (keep base ttfb metrics).
- **Context:** `tts_plugin.py:32-107`; `stt_plugin.py:55-85,148-178`; livekit-agents `tts/stream_adapter.py:89-148`, `tts/tts.py:417-462,484-499,755-808`; `agent.py:515-519` (find livekit-agents under the venv site-packages).
- **Verify:** From `infra/pi/agent`: `.venv/bin/python -c "import tts_plugin"` imports clean. Full check in Task 6.

### Task 3: Wire the flag in `agent.py`

- **Files:** `infra/pi/agent/agent.py`
- **Depends on:** Task 1, Task 2
- **Scope:** S
- **What:** Pass `streaming=cfg.tts_streaming` into the `DesktopTTS(...)` constructor.
- **How:** At `agent.py:208-212` add `streaming=cfg.tts_streaming,`. Update the barge-in comment block (181-192) to note that the streaming path drops+reconnects the persistent socket on interruption (ADR-0006 preserved).
- **Context:** `agent.py:193-216`; `config.py` `tts_streaming`; `tts_plugin.DesktopTTS` signature.
- **Verify:** From `infra/pi/agent`: `.venv/bin/python -c "import agent"` imports clean.

### Task 4: Streaming test infra + tests

- **Files:** `infra/pi/agent/tests/conftest.py`, `infra/pi/agent/tests/test_tts_plugin.py`
- **Depends on:** Task 2
- **Scope:** M
- **What:** Add a looping fake `/tts` server mode and streaming-path tests; leave the existing one-shot tests intact.
- **How:**
  - In `conftest.py`, add a server (new class or a `loop=True` flag on the existing fake) whose handler `async for msg in ws:` reads each text message, streams its chunks, sends `{"done":true}`, and keeps `connections`/active counters + a `disconnected_event`. Expose `received` (request dicts).
  - In `test_tts_plugin.py`, add tests driving `DesktopTTS(streaming=True).stream()` via `push_text`/`flush`/`end_input`: (1) two sentences over ONE connection → connections == 1, both request dicts received, PCM emitted in order; (2) pipelined ordering — sentence-2 PCM follows sentence-1 PCM with no loss; (3) `{"error"}` frame → `APIError`; (4) barge-in: cancel mid-stream → socket dropped (`disconnected_event`), and a subsequent `stream()` reconnects (connections == 2) and synthesizes cleanly.
  - Add a helper to drive the stream (feed text, `end_input()`, collect frames), adapting the existing `_collect`.
- **Context:** `tests/conftest.py`; `tests/test_tts_plugin.py:25-130,141-219`; `stt_plugin.py:148-156` for the cancel contract.
- **Verify:** From `infra/pi/agent`: `.venv/bin/python -m pytest tests/test_tts_plugin.py -q` — all old + new tests green.

### Task 5: Desktop server multi-message contract test

- **Files:** `infra/desktop/tts/tests/test_server.py`
- **Depends on:** none
- **Scope:** S
- **What:** Lock the contract the streaming plugin depends on: one connection serves multiple sequential text messages, each `PCM…{"done":true}`.
- **How:** Extend the `test_ws_streams_chunks_then_done` style (lines 65-85): within one `websocket_connect`, send two text messages and assert two `{"done":true}` terminators with correct chunks each. Monkeypatch the per-text PCM generator as that test already does.
- **Context:** `infra/desktop/tts/tests/test_server.py:65-85`; `infra/desktop/tts/server.py:72-121`.
- **Verify:** From the desktop tts service dir: run `python -m pytest tests/test_server.py -q` in its venv.

### Task 6: Validation

- **Files:** —
- **Depends on:** all
- **Scope:** S
- **What:** Run full agent + desktop tts suites and lint.
- **How:** From `infra/pi/agent`: `.venv/bin/python -m pytest tests/ -q`. Run `ruff check tts_plugin.py config.py agent.py` if ruff is configured. From the desktop tts service dir, run its pytest suite.
- **Context:** `infra/pi/agent/README.md` (test command); project config files for lint.
- **Verify:** All suites pass; no lint regressions.

## Execution

- **Mode:** sub-agents
- **Parallel:** true
- **Reasoning:** 6 tasks, single Python codebase, with a real parallel group (Tasks 1, 2, 5 share no files) followed by dependent waves.
- **Order:**
  Group 1 (parallel): Task 1, Task 2, Task 5
  ─── barrier ───
  Group 2 (parallel): Task 3, Task 4
  ─── barrier ───
  Group 3: Task 6

## Verification

- Continuous, natural-sounding playback across sentence boundaries; measurably reduced inter-segment gaps (the `TTS ttfb` metric continues to fire per response; manual listening confirms smoothness).
- Barge-in still cancels promptly (ADR-0006 preserved) — drop-on-cancel reconnect verified by test.
- `infra/pi/agent` full pytest suite green (existing one-shot + new streaming tests).
- `infra/desktop/tts` server test suite green.
