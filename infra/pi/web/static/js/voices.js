// Voice selector — lists the switchable CustomVoice preset speakers from the
// backend (GET /voices), highlights the active one, and switches it (POST /voice)
// (ADR-0020 / #48). The list is NOT hardcoded here: whatever the backend returns
// is rendered, so adding/removing a preset server-side needs no frontend change.
// `fetchImpl` is injectable so the pure render + switch logic is testable without
// a browser.

import { esc } from './html.js';

// Pure: build the selector markup from a {active, voices:[{id,active}]} state.
// The active voice gets the `active` class + aria-pressed. Empty → a hint.
export function buildVoicesHtml(state) {
  const voices = (state && state.voices) || [];
  if (!voices.length) return '<div class="ops-empty">нет голосов</div>';
  const active = state && state.active;
  return voices.map((v) => {
    const on = !!v.active || v.id === active;
    return (
      `<button type="button" class="voice-btn${on ? ' active' : ''}" ` +
      `data-voice="${esc(v.id)}"${on ? ' aria-pressed="true"' : ''}>` +
      `${esc(v.id)}</button>`
    );
  }).join('');
}

export function createVoices(el, opts = {}) {
  const log = opts.log || (() => {});
  const fetchImpl = opts.fetchImpl
    || (typeof fetch !== 'undefined' ? fetch.bind(globalThis) : null);
  let current = null;  // last known {active, voices}

  function apply(state) {
    current = state;
    if (el) el.innerHTML = buildVoicesHtml(state);
  }

  // Load the persisted global on startup so the UI reflects the active Voice.
  async function load() {
    if (!fetchImpl) return;
    try {
      const res = await fetchImpl('/voices');
      if (!res.ok) { log('voices: GET /voices ' + res.status); return; }
      apply(await res.json());
    } catch (e) {
      log('voices: load failed ' + (e && e.message ? e.message : e));
    }
  }

  // Switch the active Voice. No-op if it is already active. On success the backend
  // echoes the new state, which re-renders the highlight.
  async function select(id) {
    if (!id || !fetchImpl) return;
    if (current && current.active === id) return;
    try {
      const res = await fetchImpl('/voice', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ voice: id }),
      });
      if (!res.ok) { log('voices: switch rejected ' + id + ' (' + res.status + ')'); return; }
      apply(await res.json());
      log('voices: switched to ' + id);
    } catch (e) {
      log('voices: switch failed ' + (e && e.message ? e.message : e));
    }
  }

  function onClick(ev) {
    const btn = ev.target && ev.target.closest && ev.target.closest('[data-voice]');
    if (!btn) return undefined;
    return select(btn.dataset.voice);  // returned so callers/tests can await
  }

  if (el && el.addEventListener) el.addEventListener('click', onClick);

  return { load, apply, select, onClick, get state() { return current; } };
}
