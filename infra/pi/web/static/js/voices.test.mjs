// Zero-dependency Node ESM test for voices.js (voice selector).
// Run: node infra/pi/web/static/js/voices.test.mjs
import assert from 'node:assert/strict';
import { buildVoicesHtml, createVoices } from './voices.js';

let assertions = 0;
function ok(cond, msg) { assert.ok(cond, msg); assertions++; }
function eq(a, b, msg) { assert.deepEqual(a, b, msg); assertions++; }

const STATE = {
  active: 'ryan',
  voices: [
    { id: 'ryan', active: true },
    { id: 'aiden', active: false },
    { id: 'serena', active: false },
  ],
};

// ── buildVoicesHtml (pure) ──────────────────────────────────────────────────

// 1. renders one button per voice, highlighting the active one only.
{
  const html = buildVoicesHtml(STATE);
  eq((html.match(/class="voice-btn/g) || []).length, 3, 'one button per voice');
  eq((html.match(/voice-btn active/g) || []).length, 1, 'exactly one active');
  ok(html.includes('data-voice="ryan"') && html.includes('aria-pressed="true"'), 'active flagged');
  ok(/data-voice="aiden"[^>]*>aiden<\/button>/.test(html), 'inactive rendered, not highlighted');
}

// 2. empty catalog → hint, no buttons.
{
  const html = buildVoicesHtml({ active: null, voices: [] });
  ok(!html.includes('voice-btn'), 'no buttons when empty');
  ok(html.includes('нет голосов'), 'shows empty hint');
}

// 3. escapes ids (defensive against a malicious/odd backend value).
{
  const html = buildVoicesHtml({ active: null, voices: [{ id: '<x>', active: false }] });
  ok(html.includes('&lt;x&gt;') && !html.includes('<x>'), 'id escaped');
}

// ── createVoices (fetch injected) ───────────────────────────────────────────

function fakeEl() {
  return { innerHTML: '', addEventListener() {} };
}
function fakeFetch(routes) {
  const calls = [];
  const impl = async (url, init) => {
    calls.push({ url, init });
    const r = routes[url + (init && init.method ? ':' + init.method : '')] || routes[url];
    if (!r) return { ok: false, status: 404, json: async () => ({}) };
    return { ok: r.ok !== false, status: r.status || 200, json: async () => r.body };
  };
  impl.calls = calls;
  return impl;
}

// 4. load() GETs /voices and renders the active highlight.
{
  const el = fakeEl();
  const fetchImpl = fakeFetch({ '/voices': { body: STATE } });
  const c = createVoices(el, { fetchImpl });
  await c.load();
  ok(el.innerHTML.includes('voice-btn active'), 'load rendered active');
  eq(c.state.active, 'ryan', 'state stored');
}

// 5. select() POSTs the choice and applies the returned state.
{
  const el = fakeEl();
  const after = { active: 'aiden', voices: STATE.voices.map(v => ({ id: v.id, active: v.id === 'aiden' })) };
  const fetchImpl = fakeFetch({ '/voices': { body: STATE }, '/voice:POST': { body: after } });
  const c = createVoices(el, { fetchImpl });
  await c.load();
  await c.select('aiden');
  const post = fetchImpl.calls.find(x => x.init && x.init.method === 'POST');
  ok(post, 'POST issued');
  eq(JSON.parse(post.init.body), { voice: 'aiden' }, 'POST body carries the voice');
  eq(c.state.active, 'aiden', 'active updated from response');
  ok(/data-voice="aiden"[^>]*class=|voice-btn active[^>]*data-voice="aiden"|aiden/.test(el.innerHTML), 'aiden rendered');
}

// 6. select() of the already-active voice is a no-op (no POST).
{
  const el = fakeEl();
  const fetchImpl = fakeFetch({ '/voices': { body: STATE } });
  const c = createVoices(el, { fetchImpl });
  await c.load();
  await c.select('ryan');
  ok(!fetchImpl.calls.some(x => x.init && x.init.method === 'POST'), 'no POST for active voice');
}

// 7. a rejected switch (non-ok) keeps the previous state.
{
  const el = fakeEl();
  const fetchImpl = fakeFetch({ '/voices': { body: STATE }, '/voice:POST': { ok: false, status: 400, body: {} } });
  const c = createVoices(el, { fetchImpl });
  await c.load();
  await c.select('aiden');
  eq(c.state.active, 'ryan', 'state unchanged on rejection');
}

// 8. onClick delegates to select() via data-voice.
{
  const el = fakeEl();
  const after = { active: 'serena', voices: STATE.voices.map(v => ({ id: v.id, active: v.id === 'serena' })) };
  const fetchImpl = fakeFetch({ '/voices': { body: STATE }, '/voice:POST': { body: after } });
  const c = createVoices(el, { fetchImpl });
  await c.load();
  await c.onClick({ target: { closest: (sel) => (sel === '[data-voice]' ? { dataset: { voice: 'serena' } } : null) } });
  eq(c.state.active, 'serena', 'click switched voice');
}

console.log(`voices.test.mjs: ${assertions} assertions passed`);
