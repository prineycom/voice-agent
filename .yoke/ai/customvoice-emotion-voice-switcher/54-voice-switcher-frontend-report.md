# Report — #54 Voice-switcher frontend: voice selector in the UI

**Issue:** https://github.com/prineycom/voice-agent/issues/54 (parent #48, blocked by #53) · **Status:** complete, smoke-verified against the live server.

## What changed

- `infra/pi/web/static/js/voices.js` (new, vanilla ES module, no build step):
  `buildVoicesHtml(state)` (pure render, active highlight, escapes ids) +
  `createVoices(el, {fetchImpl, log})`. `load()` GETs `/voices` and renders;
  `select(id)` POSTs `/voice` (no-op if already active; rejection keeps prior state);
  click delegation via `data-voice`. The list is rendered from the backend response — **not
  hardcoded** — so server-side catalog changes need no frontend edit.
- `infra/pi/web/index.html`: a "🗣️ Голос" panel with `#voices` in the shared aside.
- `infra/pi/web/static/js/main.js`: import + `createVoices(...)` + `voices.load()` on page
  load (same-origin, independent of the LiveKit connection → reflects the persisted global).
- `infra/pi/web/static/css/styles.css`: `.voices` / `.voice-btn` / `.voice-btn.active`
  (pill buttons; active = the primary-blue highlight).

## Verification

- Unit: `voices.test.mjs` — **16 assertions** (render/highlight/empty/escape; load GET;
  select POST body + state apply; already-active no-op; rejection keeps state; click
  delegation). `node infra/pi/web/static/js/voices.test.mjs`.
- Live (loopback web server): `/` serves the voice panel; `voices.js` serves 200 as an ES
  module and is imported by `main.js`; `/voices` returns the catalog + active flag.
- Cross-browser: vanilla ES modules + `fetch` + `closest`, no build step — the same code the
  Kiosk and Web share. Visual/kiosk check at the epic review with the live deploy.

## AC status

- [x] lists voices from the backend GET (not hardcoded) + highlights active
- [x] choosing POSTs the choice → agent switches on next utterance (backend live getter, #53)
- [x] active reflected on load (loads the persisted global same-origin)
- [x] no build step (vanilla ES module)
- [~] consistent across browsers / kiosk visual — epic review
