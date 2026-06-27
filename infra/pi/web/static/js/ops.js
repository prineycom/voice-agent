// Background-operations + tool-call visualization driven by LiveKit data
// messages on the `voiceagent` topic.
export function createOps(opsEl, toolfeedEl, { log }) {
  let opsState = { running: [], queued: [] };  // last task snapshot
  let opsBase = 0;                  // performance.now() when the snapshot arrived (for live elapsed)
  let opsTick = null;               // interval id for the live elapsed counter

  // --- Tool-calling / background-operations visualization (LiveKit data msgs) ---
  function wire(room) {
    const { RoomEvent } = window.LivekitClient;
    room.on(RoomEvent.DataReceived, (payload, _participant, _kind, topic) => {
      if (topic !== 'voiceagent') return;
      let evt;
      try { evt = JSON.parse(new TextDecoder().decode(payload)); } catch { return; }
      if (evt.type === 'tasks') renderOps(evt);
      else if (evt.type === 'event') addToolEvent(evt);
    });
  }

  function renderOps(snapshot) {
    opsState = { running: snapshot.running || [], queued: snapshot.queued || [] };
    opsBase = performance.now();
    drawOps();
  }

  function drawOps() {
    const { running, queued } = opsState;
    if (!running.length && !queued.length) {
      opsEl.innerHTML = '<div class="ops-empty">нет активных</div>';
      return;
    }
    const dt = (performance.now() - opsBase) / 1000;
    const rows = [];
    for (const op of running) {
      const secs = Math.max(0, Math.round((op.elapsed || 0) + dt));
      rows.push(`<div class="op"><span class="spin">⏳</span><span class="lbl">${esc(op.label)}</span><span class="t">${secs}s</span></div>`);
    }
    for (const label of queued) {
      rows.push(`<div class="op queued"><span>⌛</span><span class="lbl">${esc(label)}</span><span class="t">очередь</span></div>`);
    }
    opsEl.innerHTML = rows.join('');
  }

  function addToolEvent(evt) {
    const empty = toolfeedEl.querySelector('.ops-empty');
    if (empty) empty.remove();
    const div = document.createElement('div');
    const time = new Date().toLocaleTimeString();
    if (evt.kind === 'delegated') {
      div.className = 'tf';
      div.innerHTML = `<div class="tf-head">🔧 delegate «${esc(evt.label)}»</div>`;
    } else if (evt.kind === 'done' || evt.kind === 'error') {
      div.className = 'tf ' + evt.kind;
      const icon = evt.kind === 'done' ? '✅' : '❌';
      let html = `<div class="tf-head">${icon} ${esc(evt.label)}</div>`;
      if (evt.summary) html += `<div class="tf-sum">${esc(evt.summary)}</div>`;
      if (evt.full && evt.full.length > (evt.summary || '').length) {
        html += `<details><summary>полный вывод</summary><pre>${esc(evt.full)}</pre></details>`;
      }
      div.innerHTML = html;
    } else if (evt.kind === 'cancelled') {
      div.className = 'tf cancelled';
      div.innerHTML = `<div class="tf-head">✖️ отменено задач: ${evt.count || ''}</div>`;
    } else {
      return;
    }
    div.title = time;
    toolfeedEl.appendChild(div);
    toolfeedEl.scrollTop = toolfeedEl.scrollHeight;
  }

  function resetOpsUI() {
    opsState = { running: [], queued: [] };
    drawOps();
    toolfeedEl.innerHTML = '<div class="ops-empty">пока пусто</div>';
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  function startTick() {
    if (opsTick) clearInterval(opsTick);
    opsTick = setInterval(drawOps, 1000);  // live-tick the running-task seconds
  }

  function stopTick() {
    if (opsTick) { clearInterval(opsTick); opsTick = null; }
  }

  return { wire, startTick, stopTick, reset: resetOpsUI };
}
