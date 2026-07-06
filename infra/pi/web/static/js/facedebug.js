// A2F debug face: a canvas schematic face + timing readout driven DIRECTLY from
// the raw ARKit blendshape frames, bypassing Live2D. Bisects "no facial
// animation": if this face moves, blendshapes reach the browser; the timing
// panel then shows HOW they arrive (steady vs a burst faster than real time).
//
// Activated by `?facedebug=1`. Non-destructive overlay; motions are frozen by
// main.js. Per-stream stats also go to console.log('[a2f] …') and window.__a2fStats.

const g = (a, k) => (a && a[k]) || 0;
const c01 = v => (v < 0 ? 0 : v > 1 ? 1 : v);
const nowMs = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());

export function createFaceDebug() {
  const panel = document.createElement('div');
  panel.style.cssText =
    'position:fixed;top:8px;right:8px;z-index:99999;width:290px;padding:8px;' +
    'background:rgba(12,14,20,.94);border:1px solid #2b3350;border-radius:10px;' +
    'font:11px/1.35 ui-monospace,Menlo,Consolas,monospace;color:#cfd6e6;box-shadow:0 6px 24px rgba(0,0,0,.45)';
  const title = document.createElement('div');
  title.textContent = 'A2F debug face  (?facedebug=1)';
  title.style.cssText = 'margin-bottom:6px;color:#8ab4ff;font-weight:600';
  const canvas = document.createElement('canvas');
  canvas.width = 274; canvas.height = 150;
  canvas.style.cssText = 'width:274px;height:150px;background:#0b0e16;border-radius:6px;display:block';
  const info = document.createElement('div');
  info.style.cssText = 'margin-top:6px;white-space:pre;color:#9fb0d0';
  const histEl = document.createElement('div');
  histEl.style.cssText = 'margin-top:6px;white-space:pre;color:#7f8fb0;border-top:1px solid #232a44;padding-top:4px;max-height:150px;overflow:auto';
  panel.appendChild(title); panel.appendChild(canvas); panel.appendChild(info); panel.appendChild(histEl);
  document.body.appendChild(panel);

  const ctx = canvas.getContext('2d');
  let frames = 0, streams = 0, last = null;
  // current-stream accumulators
  let s = null;
  const history = [];

  function draw(a) {
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    ctx.lineWidth = 2;
    const cx = W / 2, cy = H / 2;
    const openL = c01(1 - g(a, 'EyeBlinkLeft'));
    const openR = c01(1 - g(a, 'EyeBlinkRight'));
    const gx = Math.max(-1, Math.min(1, ((g(a,'EyeLookOutLeft')-g(a,'EyeLookInLeft'))+(g(a,'EyeLookInRight')-g(a,'EyeLookOutRight')))/2));
    const gy = Math.max(-1, Math.min(1, ((g(a,'EyeLookUpLeft')+g(a,'EyeLookUpRight'))-(g(a,'EyeLookDownLeft')+g(a,'EyeLookDownRight')))/2));
    const browL = g(a,'BrowInnerUp') + g(a,'BrowOuterUpLeft') - g(a,'BrowDownLeft');
    const browR = g(a,'BrowInnerUp') + g(a,'BrowOuterUpRight') - g(a,'BrowDownRight');
    const eye = (ex, open, brow) => {
      ctx.strokeStyle = '#7f8fc0';
      ctx.beginPath(); ctx.moveTo(ex-16, cy-30-brow*10); ctx.lineTo(ex+16, cy-30-brow*10); ctx.stroke();
      ctx.strokeStyle = '#8ab4ff';
      ctx.beginPath(); ctx.ellipse(ex, cy-12, 18, (3 + open*18)/2, 0, 0, Math.PI*2); ctx.stroke();
      ctx.fillStyle = '#8ab4ff';
      ctx.beginPath(); ctx.arc(ex + gx*8, cy-12 + gy*5, Math.max(1.5, 3*open), 0, Math.PI*2); ctx.fill();
    };
    eye(cx-38, openL, browL); eye(cx+38, openR, browR);
    const open = c01(g(a,'JawOpen') * (1 - g(a,'MouthClose')));
    const form = (g(a,'MouthSmileLeft')+g(a,'MouthSmileRight'))/2 - (g(a,'MouthFrownLeft')+g(a,'MouthFrownRight'))/2;
    ctx.strokeStyle = '#ff9e64';
    ctx.beginPath(); ctx.ellipse(cx, cy+38, (44 + form*18)/2, (3 + open*38)/2, 0, 0, Math.PI*2); ctx.stroke();
  }

  function renderLive() {
    const open = c01(g(last,'JawOpen') * (1 - g(last,'MouthClose')));
    const wall = s ? Math.round(s.lastWall - s.firstWall) : 0;
    info.textContent =
      `frames:${frames}  streams:${streams}  keys:${last?Object.keys(last).length:0}\n` +
      `JawOpen:${g(last,'JawOpen').toFixed(3)}  mouthOpen:${open.toFixed(3)}\n` +
      `EyeBlink L/R:${g(last,'EyeBlinkLeft').toFixed(2)}/${g(last,'EyeBlinkRight').toFixed(2)}\n` +
      `this stream: ${s ? s.count : 0}f  wall:${wall}ms`;
  }

  function onStart() {
    streams++;
    s = { count: 0, firstWall: nowMs(), lastWall: nowMs(), prevWall: nowMs(), gapMax: 0, firstT: null, lastT: null };
  }

  function onFrame(evt) {
    frames++;
    if (!s) onStart();
    const w = nowMs();
    s.count++;
    if (s.count === 1) s.firstWall = w;
    else { const gap = w - s.prevWall; if (gap > s.gapMax) s.gapMax = gap; }
    s.prevWall = w; s.lastWall = w;
    const t = evt.t;
    if (typeof t === 'number') { if (s.firstT === null) s.firstT = t; s.lastT = t; }
    last = evt.arkit || {};
    draw(last);
    renderLive();
  }

  function onEnd() {
    if (!s || s.count === 0) return;
    const wall = Math.max(0, s.lastWall - s.firstWall);           // ms spent receiving
    const tspan = (s.firstT !== null && s.lastT !== null) ? (s.lastT - s.firstT) * 1000 : NaN; // ms of audio the frames cover
    const fps = wall > 0 ? Math.round((s.count - 1) / (wall / 1000)) : 0;
    const ratio = (wall > 0 && !isNaN(tspan)) ? (tspan / wall).toFixed(1) : '?';
    const line = `#${streams}: ${s.count}f recv=${Math.round(wall)}ms audio=${isNaN(tspan)?'?':Math.round(tspan)+'ms'} fps=${fps} gap=${Math.round(s.gapMax)}ms x${ratio}`;
    history.unshift(line);
    if (history.length > 8) history.pop();
    histEl.textContent = history.join('\n');
    try { console.log('[a2f] ' + line); } catch (e) {}
    try { window.__a2fStats = { frames, streams, history: history.slice() }; } catch (e) {}
    s = null;
  }

  draw({});
  info.textContent = 'frames:0\nwaiting for A2F blendshapes…\n(speak to the agent)';
  histEl.textContent = 'per-stream: <count> recv=<ms to receive> audio=<ms covered> fps=<rate> gap=<max pause> x<audio/recv>\n(x >> 1 means frames arrive as a burst, faster than real time)';
  return { onFrame, onStart, onEnd };
}
