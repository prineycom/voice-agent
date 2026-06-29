# Plan: 30-responsive-desktop-mobile-polish

**Issue:** #30 — Responsive desktop+mobile + polish (Epic 5, final Web Frontend slice)
**Mode:** sub-agents
**Parallel:** false
**Update docs:** false

## Summary

Make the web frontend (`infra/pi/web/`) universally responsive from wide desktop
down to a narrow phone. On small screens the tool/ops `<aside>` sidebar collapses
into a toggleable off-canvas overlay (driven by one CSS class flipped by a header
`☰` button), while the avatar + transcript stay primary. The connection-status,
agent-state, and VU meter stay visible/legible at every breakpoint — which forces
relocating the VU bar out of the (collapsible) sidebar into the always-visible
header. No build step (ADR 0011): native `@media`, CSS grid/flex, `transform`.
Kiosk mode stays out of scope (Epic 6).

## Design decisions

### DD-1 — Single primary breakpoint `@media (max-width: 768px)`
- **Decision:** Desktop stays the CSS default (current rules unchanged); a single
  `max-width:768px` block holds every mobile override.
- **Rationale:** The only structural reflow needed is sidebar→overlay; the header
  already `flex-wrap:wrap`s (`styles.css:9`) so intermediate widths need no extra
  query. The 280px sidebar (`styles.css:30`) is fine at any ≥768px width.
- **Alternative:** A multi-breakpoint ladder (1100/768/420) — rejected: extra diff
  and risk for no acceptance criterion.

### DD-2 — VU + connection + agent-state live in the HEADER (always visible)
- **Decision:** Keep `#connState`/`#agentState` in the header (already there,
  `index.html:12-13`); **relocate** the VU block (`.vu`/`#vuBar`) + `#micLabel`
  from the sidebar Микрофон panel into the header.
- **Rationale:** AC requires the VU meter visible at all breakpoints; when the
  sidebar becomes a hidden overlay, a VU left inside it vanishes when collapsed.
  Preserving `id="vuBar"`/`id="micLabel"` exactly → zero JS churn (`vu.js`,
  `main.js:19-20,84,86,102` keep working).
- **Alternative:** Duplicate the VU into the header — rejected: two elements with
  the same id, and `vu.js` writes one node.

### DD-3 — Sidebar overlay = pure CSS, toggled by one class
- **Decision:** On mobile `aside` becomes `position:fixed; transform:translateX(-100%)`,
  width `min(320px,85vw)`, `z-index:50`, opaque background; `.open` sets
  `translateX(0)`. A header `#sidebarBtn` (`☰`, hidden on desktop) toggles the class;
  a `#scrim` dims the page and closes on click. JS only flips the class.
- **Rationale:** Mirrors the existing `muteBtn`/`connectBtn` toggle pattern
  (`main.js:99-103`) and `classList.toggle` precedent (`transcript.js:30`). Native
  `transition: transform` (GPU, no reflow) — no animation lib (no-build constraint).
- **Alternative:** JS-driven inline styles — rejected: CSS-class toggle is the
  established idiom and keeps layout in the stylesheet.

### DD-4 — Avatar box stays non-zero on mobile (NaN guard)
- **Decision:** Mobile stage stacks with min-heights: `#avatar { flex:0 0 auto;
  min-height:38vh }` and `#transcript { flex:1 1 auto; min-height:30vh }`. Never
  `display:none` the avatar.
- **Rationale:** `avatar.js:78-84 layout()` divides by `app.renderer.screen` dims;
  a 0-height `#avatar` → NaN scale / vanished model. Non-zero boxes keep the
  ResizeObserver (`avatar.js:87-88`) re-layout safe.
- **Alternative:** Hide the avatar on mobile — rejected: avatar is a primary focus
  per the AC, and hiding risks the NaN path on re-show.

### DD-5 — No CSS-variable / color-token refactor
- **Decision:** Keep existing hex literals; do not promote them to `:root` vars.
- **Rationale:** Optional polish that balloons the diff across the whole file and
  no AC requires it. This issue is layout + overlay only.
- **Alternative:** Token refactor — deferred.

### DD-6 — Polish stays within existing scroll containers
- **Decision:** Reuse `#toolfeed max-height:220px` (`styles.css:69`) and `#log
  max-height:160px` (inline) — they already scroll inside `aside { overflow-y:auto }`.
  Bump `.msg max-width:80%`→`90%` on mobile only.
- **Rationale:** Feeds stay readable/scrollable in the overlay with no new mechanism.

### DD-7 — Kiosk stays out (Epic 6)
- **Decision:** No auto-connect, no Chromium flags, no kiosk branch. Responsive +
  fullscreen-capable design is inherently kiosk-friendly.

## Tasks

### Task 1: HTML — header VU relocation + toggle + scrim
- **Files:** `infra/pi/web/index.html`
- **Depends on:** none
- **Scope:** S (structure only)
- **What:** Add the `☰` sidebar toggle button to the header, move the VU block +
  mic label out of the sidebar into the header, and add the overlay scrim element.
- **How:**
  (a) Add `<button id="sidebarBtn" aria-label="Меню" aria-expanded="false">☰</button>`
  as the first child of `<header>` (before `<h1>`, ~L11).
  (b) **Move** `<div class="vu"><div id="vuBar"></div></div>` (L27) and
  `<small id="micLabel">…</small>` (L28) out of the aside Микрофон panel into the
  header, wrapped in `<div class="mic">…</div>`, placed before `<span class="grow">` (L14).
  (c) Delete the now-empty Микрофон `.panel` (L25-29).
  (d) Add `<div id="scrim"></div>` as the last child of `<main>` (after `.stage`, ~L51).
  Keep `id="vuBar"`/`id="micLabel"` EXACTLY — JS depends on them. Leave Latency/ops/tools/log panels untouched.
- **Context:** `infra/pi/web/index.html` (header 10-17, aside 19-42, .stage 43-51).
  ids feed `main.js:19-20,84,86,102` and `vu.js`. DD-2, DD-3.
- **Verify:** `grep -c 'id="vuBar"' infra/pi/web/index.html` == 1 and
  `grep -c 'id="micLabel"' infra/pi/web/index.html` == 1; `#sidebarBtn` + `#scrim` present once each.

### Task 2: CSS base — header mic cluster, hide toggle/scrim on desktop
- **Files:** `infra/pi/web/static/css/styles.css`
- **Depends on:** Task 1
- **Scope:** S (desktop-neutral additions)
- **What:** Style the relocated header VU cluster and hide the mobile-only controls
  on desktop, without altering the desktop layout.
- **How:** Add `.mic { display:flex; align-items:center; gap:8px; }` and constrain
  the header VU width `header .vu { width:90px; flex:0 0 90px; }` (the `.vu` base at
  L52-53 still applies). Add `#sidebarBtn { display:none; }` and `#scrim { display:none; }`
  (shown only inside the @media block). Do NOT touch `main`/`aside`/`.stage`/`#avatar`/`#transcript` here.
- **Context:** `infra/pi/web/static/css/styles.css` (badge/button rules ~22-29, `.vu` 52-53). DD-2, DD-3.
- **Verify:** Desktop render unchanged except the VU now sits in the header;
  `#sidebarBtn`/`#scrim` invisible. (Manual; CSS not CI-testable.)

### Task 3: CSS responsive — `@media (max-width:768px)` overlay block
- **Files:** `infra/pi/web/static/css/styles.css`
- **Depends on:** Task 2 (same file — strictly sequential)
- **Scope:** M (mobile layout)
- **What:** Add the first `@media (max-width:768px)` block converting the grid to a
  single column and the sidebar to an off-canvas overlay, with avatar/transcript
  min-heights and header tightening.
- **How:** Single appended `@media (max-width:768px){ … }` containing:
  - `main { grid-template-columns: 1fr; }` (override L30)
  - `aside { position:fixed; top:0; left:0; bottom:0; width:min(320px,85vw); transform:translateX(-100%); transition:transform .2s ease; z-index:50; background:#0e1116; padding-top:16px; }` and `aside.open { transform:translateX(0); }` (keeps L31 `overflow-y:auto`)
  - `#sidebarBtn { display:inline-flex; }`
  - `#scrim { display:none; position:fixed; inset:0; background:rgba(0,0,0,.5); z-index:40; }` and `#scrim.open { display:block; }`
  - `#avatar { flex:0 0 auto; min-height:38vh; }` (override L35 — NaN guard)
  - `#transcript { flex:1 1 auto; min-height:30vh; }` (override L54)
  - `.msg { max-width:90%; }` (override L55)
  - `header h1 { font-size:14px; }` (allow wrap; keep VU/badges visible)
- **Context:** `aside` opaque so the overlay covers the stage; `z-index` above the
  PIXI canvas (`#avatar canvas`, L39) and absolute hint/credit (L40-47). DD-1, DD-3, DD-4, DD-6.
- **Verify:** At <768px the sidebar is off-canvas, `.open` slides it in, avatar +
  transcript are both non-zero and scrollable, the header keeps connState/agentState/VU. (Manual.)

### Task 4: JS — wire sidebar toggle + scrim
- **Files:** `infra/pi/web/static/js/main.js`
- **Depends on:** Task 1 (ids must exist)
- **Scope:** S (DOM wiring only)
- **What:** Resolve the new elements and wire the open/close toggle.
- **How:** After the existing lookups (L14-24) add
  `const sidebarBtn = document.getElementById('sidebarBtn');`,
  `const asideEl = document.querySelector('aside');`,
  `const scrimEl = document.getElementById('scrim');`. Add
  `function setSidebar(open){ asideEl.classList.toggle('open', open); scrimEl.classList.toggle('open', open); sidebarBtn.setAttribute('aria-expanded', String(open)); }`.
  Wire `sidebarBtn.onclick = () => setSidebar(!asideEl.classList.contains('open'));`
  and `scrimEl.onclick = () => setSidebar(false);` near the `muteBtn.onclick` block (~L99).
  Mirror the direct-assignment style; no delegation.
- **Context:** `infra/pi/web/static/js/main.js` (lookups 14-30, muteBtn.onclick 99-103).
  classList toggle precedent `transcript.js:30`. DD-3.
- **Verify:** `node --check infra/pi/web/static/js/main.js` passes; tapping `☰`
  opens/closes the overlay, scrim click closes, aria-expanded flips. (Last part manual.)

### Task 5: Validation
- **Files:** — (read-only)
- **Depends on:** Task 1, Task 2, Task 3, Task 4
- **Scope:** S (regression + manual checklist)
- **What:** Confirm no regression and run the manual breakpoint checklist.
- **How / Verify:**
  1. `node infra/pi/web/static/js/lipsync.test.mjs && node infra/pi/web/static/js/motion.test.mjs` → both pass.
  2. `node --check infra/pi/web/static/js/main.js` → syntax OK.
  3. `grep -c 'id="vuBar"' infra/pi/web/index.html` == 1 (no duplicate id from the relocation).
  4. `grep -ri 'kiosk\|autoconnect\|--kiosk' infra/pi/web/static infra/pi/web/index.html` → empty (no kiosk code).
  5. **MANUAL breakpoint checklist on the Pi** (CSS layout not CI-testable — no headless browser). For widths **1440 / 1024 / 768 / 414 / 360 px** confirm:
     - [ ] No horizontal overflow / no clipped panel at any width
     - [ ] ≥768px: sidebar is the fixed left column; `#sidebarBtn`/`#scrim` hidden
     - [ ] <768px: sidebar off-canvas; `☰` slides it in as an overlay; scrim dismisses; avatar + transcript stay primary
     - [ ] connState + agentState + VU bar visible/legible at EVERY width incl. sidebar collapsed
     - [ ] transcript + tool/ops feed scroll independently on mobile (inside the overlay)
     - [ ] Live2D avatar renders (non-zero box, no NaN/vanish) at every width and after toggle

## Execution

- **Mode:** sub-agents
- **Parallel:** false
- **Reasoning:** 3 files, ~120 lines. `styles.css` is written by both Task 2 and
  Task 3 (strictly sequential), and Task 1 (`index.html`) gates Tasks 2/3/4 (they
  style/wire elements Task 1 creates). The shared files force a chain; fabricated
  parallelism would only add hand-off cost.
- **Order:** T1 → T2 → T3 → T4 → T5 (sequential).

```
T1 (html) ──┬──► T2 (css base) ──► T3 (css @media) ──┐
            └──► T4 (js wiring) ───────────────────────┤
                                                       └──► T5 (validation)
```

## Verification

Acceptance criteria (issue #30):
- [ ] Layout adapts cleanly wide desktop → narrow mobile, no broken/overflowing panels — T3
- [ ] On small screens sidebar collapses into a toggleable overlay; avatar + transcript stay primary — T1/T3/T4
- [ ] Connection status, agent-state, VU meter remain visible/legible at all breakpoints — T1/T2 (VU → header) + T3
- [ ] Transcript and tool/ops feed stay readable and scrollable on mobile — T3/DD-6
- [ ] No kiosk-specific code added; design remains kiosk-friendly — T5 grep gate / DD-7
