# Report: 33-phase1-tts-a2f-fork

**Plan:** `.yoke/ai/33-phase1-tts-a2f-fork/33-phase1-tts-a2f-fork-plan.md`
**Mode:** sub-agents
**Status:** ✅ complete

## Tasks

| #   | Task                                   | Status  | Commit    | Concerns |
| --- | -------------------------------------- | ------- | --------- | -------- |
| 1   | `A2FFork` module                       | ✅ DONE | `7c12f41` | —        |
| 2   | `.env.example` config                  | ✅ DONE | `a710a9b` | —        |
| 3   | fake `/a2f` WS test server             | ✅ DONE | `430d11c` | —        |
| 4   | Wire fork into `server.py`             | ✅ DONE | `f48a243` | —        |
| 5   | `test_a2f_fork.py` (unit)              | ✅ DONE | `ecc404d` | —        |
| 6   | `test_server_fork.py` (integration)    | ✅ DONE | `93b86de` | —        |

One implementer, sequential. Combined review (spec + quality) returned ✅ Approved.

## Post-implementation

| Step          | Status      | Commit    |
| ------------- | ----------- | --------- |
| Validate      | ✅ pass (49 passed) | — |
| Review fix    | ✅ done (prune completed forks) | `d181d56` |
| Documentation | ➖ N/A (opt-in off) | — |
| Format        | ➖ N/A (no black/ruff config) | — |

## Review

Combined review ✅ Approved — the load-bearing property (fork never affects the `/tts` client stream or barge-in) verified on every path (happy, synth-error, client-disconnect barge-in, A2F-refused, A2F-slow-peer). One Minor finding: the connection-scoped `forks` list retained completed forks until the connection closed — since the agent pipelines many sentences over one `/tts` socket, a long kiosk session would accumulate them. Fixed in `d181d56` (added `A2FFork.done`; prune finished forks each utterance). Suite stayed green.

## Validation

`cd infra/desktop/tts && python -m pytest tests/ -q` ✅ **49 passed** (43 pre-existing `test_server`/`test_synthesize`/`test_audio` + 6 new), 1 pre-existing deprecation warning. No project lint/type-check/build tooling → N/A.

Scope confirmed clean: only `infra/desktop/tts/**` changed. No changes to the agent, the `a2f/` service, `requirements.txt`, `synthesize.py`, or `engines.py` (`websockets` dep already present).

## Changes summary

| File                                          | Action   | Description                                                                 |
| --------------------------------------------- | -------- | -------------------------------------------------------------------------- |
| infra/desktop/tts/a2f_fork.py                 | created  | `A2FFork` best-effort client: tees an utterance's emotion+PCM into `/a2f`, drains/discards frames; swallow-all failure; `feed`/`end`/`close`/`done`; `maybe_start_fork` |
| infra/desktop/tts/server.py                   | modified | Parse `emotion` on `/tts`; create/feed/end/close + prune forks alongside the untouched client drain loop |
| infra/desktop/tts/.env.example                | modified | `A2F_WS_URL` (loopback default) + `A2F_FORK_ENABLED` kill-switch            |
| infra/desktop/tts/tests/fake_a2f.py           | created  | Threaded GPU-free fake `/a2f` WS server recording the client message order |
| infra/desktop/tts/tests/test_a2f_fork.py      | created  | Unit: ordering/audio-binding, list emotion verbatim, barge-in teardown, dead-port failure isolation |
| infra/desktop/tts/tests/test_server_fork.py   | created  | Integration: `/tts` emotion forwarded end-to-end + A2F-down does not break the client |

## Commits

- `58825fc` #33 docs(33-phase1-tts-a2f-fork): add implementation plan
- `7c12f41` #33 feat(33-phase1-tts-a2f-fork): add A2FFork client that tees TTS PCM into the A2F service
- `a710a9b` #33 chore(33-phase1-tts-a2f-fork): add A2F_WS_URL / A2F_FORK_ENABLED to tts .env.example
- `430d11c` #33 test(33-phase1-tts-a2f-fork): add fake /a2f WS server test helper
- `f48a243` #33 feat(33-phase1-tts-a2f-fork): fork PCM + emotion into A2F from the /tts handler
- `ecc404d` #33 test(33-phase1-tts-a2f-fork): unit-test A2FFork ordering, cancel, failure isolation
- `93b86de` #33 test(33-phase1-tts-a2f-fork): integration-test /tts emotion forward + A2F-down isolation
- `d181d56` #33 fix(33-phase1-tts-a2f-fork): prune completed forks so long /tts connections don't retain them

## Follow-ups (Phase 3, out of scope here)

- Agent **sends** the parsed emotion tag on the `/tts` `emotion` field (`infra/pi/agent/tts_plugin.py:158,246`).
- Agent **consumes** A2F blendshape frames and forwards them onto the `voiceagent` DataChannel (requires resolving the producer/consumer split on the A2F `/a2f` socket).
