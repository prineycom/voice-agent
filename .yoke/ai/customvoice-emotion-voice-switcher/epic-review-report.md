# Review report — CustomVoice emotion + voice-switcher epic

High-effort workflow-backed code review over the epic diff (issues #50-#54). Findings
triaged below; fixes applied on the branch.

## Fixed

1. **Wire voice always a concrete preset (rollback + dead fallback)** —
   `tts_plugin.py`/`agent.py`/`voice_state.py`. The send path used
   `get_active_voice()`, which never returns falsy, so (a) an un-switched system sent
   `"ryan"` instead of the engine-agnostic `"default"` — breaking a `voice_clone`
   rollback (`_resolve("ryan")` → unknown ref) — and (b) the documented `cfg.tts_voice`
   fallback was dead code. **Fix:** new `voice_state.active_voice_or_none()` returns the
   *explicitly-persisted* preset or `None`; the plugin falls back to its constructor
   `voice` when `None`. Wired via `set_voice_source(active_voice_or_none)`. Covered by 3
   new tests.
2. **`do_POST` unhandled `OSError`** — `web/server.py`. A disk-full/permission error from
   `set_active_voice` would crash the handler. **Fix:** catch `OSError` → clean 500 JSON.
3. **Implicit `Path.home()` coupling of web↔worker state** — hardened by pinning
   `Environment=VOICE_STATE_PATH` in `voice-agent-web.service` + a sync comment. (Both
   live units run `User=priney`, so the shared path already works — verified live: the
   running worker read the value the web server persisted.)

## Refuted / accepted with rationale

- **`generate_custom_voice_streaming` kwargs may `TypeError`** — refuted: the deployed
  `faster_qwen3_tts` 0.2.6 signature was probed *and* 4 live syntheses ran with zero
  errors (neutral + 3 emotions, incl. the number+latin case).
- **Switcher catalog is engine-unaware (explicit switch under `voice_clone` errors)** —
  accepted/documented: the switcher is CustomVoice-scoped by ADR-0020, prod is
  `custom_voice`, and the unpersisted/default case is now engine-agnostic (fix #1). An
  operator explicitly switching voice while on the clone engine is an out-of-contract
  misconfiguration.
- **`esc()` duplicated in voices.js vs ops.js; sampling-knob block duplicated across two
  engines** — accepted: 2-line duplication each; extracting shared modules (and touching
  ops.js / both engines) carries more risk than the DRY benefit here.

## Post-fix verification

- Agent suite **124 passed**; Desktop `test_synthesize` **36 passed**; JS tests all pass.
- Web endpoint smoke (200 switch / 400 invalid) green after the `OSError` guard.
