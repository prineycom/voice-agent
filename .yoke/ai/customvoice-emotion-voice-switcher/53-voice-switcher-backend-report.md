# Report — #53 Voice-switcher backend: HTTP endpoint + persisted global voice

**Issue:** https://github.com/prineycom/voice-agent/issues/53 (parent #48, blocked by #50) · **Status:** complete, curl-verified locally.

## Design

The agent worker package **owns** the Voice state; the Pi web front door **exposes** it
over HTTP same-origin for the browser (ADR-0020 implementation note). No new port, no CORS,
no HTTPS mixed-content.

## What changed

- `infra/pi/agent/voice_state.py` (new, pure stdlib): preset catalog `VOICES`
  (`ryan, aiden, serena, vivian, ono_anna, sohee, uncle_fu` — `eric`/`dylan` excluded),
  `default_voice()` (`ryan`, `VOICE_DEFAULT`-overridable), atomic persist to
  `VOICE_STATE_PATH` (XDG state), mtime-cached `get_active_voice()` for the per-sentence hot
  path, `set_active_voice()` (validates → 400 on unknown), `state()` GET payload.
- `infra/pi/web/server.py`: `GET /voices` + `do_POST /voice` (same-origin, JSON), importing
  `voice_state` from the sibling agent package.
- `infra/pi/agent/tts_plugin.py`: `set_voice_source()` + `_current_voice()` read the active
  Voice live at send time (both one-shot and streaming paths); falls back to the constructor
  `voice` if the getter is unset/falsy/raises.
- `infra/pi/agent/agent.py`: wires `desktop_tts.set_voice_source(voice_state.get_active_voice)`.
- `docs/adr/0020`: implementation note on the endpoint location. `agent/.env.example`:
  `VOICE_STATE_PATH` / `VOICE_DEFAULT` documented.

## Verification

- Unit: `test_voice_state.py` (13 cases: catalog, default/override, roundtrip, invalid→raise,
  corrupt/unknown-file fallback, same-process cache invalidation, persists-across-reload,
  state payload) + `test_tts_plugin.py::test_voice_source_read_live_per_sentence`
  (live per-sentence switch + fallback on falsy/raising getter). **40 passed** (with the TTS
  suite); full agent suite green.
- Live curl (loopback web server): `GET /voices` lists + flags active; `POST /voice`
  {ryan→aiden} switches + echoes state; re-`GET` reflects it; persisted to the JSON file
  (survives restart); `POST` unknown (`eric`) and non-JSON both → **400**.

## AC status

- [x] GET lists voices + flags active
- [x] POST sets, validates, next utterance uses it (live getter), invalid rejected (400)
- [x] persists across restart (file-backed global)
- [x] no service restart / model reload (one CustomVoice model; live getter)
