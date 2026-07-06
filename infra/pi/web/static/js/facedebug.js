// A2F debug face: a canvas schematic face + numeric readout driven DIRECTLY from
// the raw ARKit blendshape frames, bypassing Live2D entirely. Purpose: bisect
// "no facial animation" — if THIS face moves while the agent speaks, the A2F
// blendshapes reach the browser correctly and the problem is downstream in the
// Live2D rig; if the frame counter stays 0, the blendshapes never arrive (agent
// forward / data-channel hop) and Live2D is not at fault.
//
// Activated by `?facedebug=1`. Non-destructive: only a fixed overlay is added and
// motion playback is suppressed by main.js; nothing in the production path changes.

const g = (a, k) => (a && a[k]) || 0;
const c01 = v => (v < 0 ? 0 : v > 1 ? 1 : v);

export function createFaceDebug() {
  const panel = document.createElement('div');
  panel.style.cssText =
    'position:fixed;top:8px;right:8px;z-index:99999;width:260px;padding:8px;' +
    'background:rgba(12,14,20,.92);border:1px solid #2b3350;border-radius:10px;' +
    'font:12px/1.35 ui-monospace,Menlo,Consolas,monospace;color:#cfd6e6;box-shadow:0 6px 24px rgba(0,0,0,.4)';
  const canvas = document.createElement('canvas');
  canvas.width = 244; canvas.height = 180;
  canvas.style.cssText = 'width:244px;height:180px;background:#0b0e16;border-radius:6px;display:block';
  const info = document.createElement('div');
  info.style.cssText = 'margin-top:6px;white-space:pre;color:#9fb0d0';
  const title = document.createElement('div');
  title.textContent = 'A2F debug face  (?facedebug=1)';
  title.style.cssText = 'margin-bottom:6px;color:#8ab4ff;font-weight:600';
  panel.appendChild(title); panel.appendChild(canvas); panel.appendChild(info);
  document.body.appendChild(panel);

  const ctx = canvas.getContext('2d');
  let frames = 0;
  let streams = 0;
  let last = null;
  let lastRxMs = 0;

  function draw(a) {
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    ctx.strokeStyle = '#5b6b96'; ctx.fillStyle = '#3a4568'; ctx.lineWidth = 2;
    const cx = W / 2, cy = H / 2;

    // Eyes: openness = 1 - blink; gaze from EyeBall*; brow height offsets the lid.
    const openL = c01(1 - g(a, 'EyeBlinkLeft'));
    const openR = c01(1 - g(a, 'EyeBlinkRight'));
    const gx = Math.max(-1, Math.min(1, ((g(a,'EyeLookOutLeft')-g(a,'EyeLookInLeft'))+(g(a,'EyeLookInRight')-g(a,'EyeLookOutRight')))/2));
    const gy = Math.max(-1, Math.min(1, ((g(a,'EyeLookUpLeft')+g(a,'EyeLookUpRight'))-(g(a,'EyeLookDownLeft')+g(a,'EyeLookDownRight')))/2));
    const browL = g(a,'BrowInnerUp') + g(a,'BrowOuterUpLeft') - g(a,'BrowDownLeft');
    const browR = g(a,'BrowInnerUp') + g(a,'BrowOuterUpRight') - g(a,'BrowDownRight');
    const eye = (ex, open, brow) => {
      ctx.strokeStyle = '#7f8fc0';
      // brow
      ctx.beginPath(); ctx.moveTo(ex-16, cy-34-brow*10); ctx.lineTo(ex+16, cy-34-brow*10); ctx.stroke();
      // eye white (height scales with openness)
      const h = 4 + open * 20;
      ctx.strokeStyle = '#8ab4ff';
      ctx.beginPath(); ctx.ellipse(ex, cy-14, 20, h/2, 0, 0, Math.PI*2); ctx.stroke();
      // pupil
      ctx.fillStyle = '#8ab4ff';
      ctx.beginPath(); ctx.arc(ex + gx*9, cy-14 + gy*6, Math.max(1.5, 3*open), 0, Math.PI*2); ctx.fill();
    };
    eye(cx-40, openL, browL);
    eye(cx+40, openR, browR);

    // Mouth: opening = JawOpen*(1-MouthClose); form = smile - frown.
    const open = c01(g(a,'JawOpen') * (1 - g(a,'MouthClose')));
    const form = (g(a,'MouthSmileLeft')+g(a,'MouthSmileRight'))/2 - (g(a,'MouthFrownLeft')+g(a,'MouthFrownRight'))/2;
    const mw = 46 + form * 18;
    const mh = 3 + open * 40;
    ctx.strokeStyle = '#ff9e64';
    ctx.beginPath(); ctx.ellipse(cx, cy+42, mw/2, mh/2, 0, 0, Math.PI*2); ctx.stroke();
  }

  function onFrame(evt) {
    frames++;
    last = evt.arkit || {};
    lastRxMs = (typeof performance !== 'undefined') ? performance.now() : 0;
    draw(last);
    const open = c01(g(last,'JawOpen') * (1 - g(last,'MouthClose')));
    info.textContent =
      `frames: ${frames}   streams: ${streams}\n` +
      `JawOpen:   ${g(last,'JawOpen').toFixed(3)}   mouthOpen: ${open.toFixed(3)}\n` +
      `EyeBlink L/R: ${g(last,'EyeBlinkLeft').toFixed(2)} / ${g(last,'EyeBlinkRight').toFixed(2)}\n` +
      `Smile L/R: ${g(last,'MouthSmileLeft').toFixed(2)} / ${g(last,'MouthSmileRight').toFixed(2)}\n` +
      `arkit keys: ${Object.keys(last).length}`;
  }

  function onStart() { streams++; }
  function onEnd() { /* keep last pose on screen */ }

  // Initial idle render + a "waiting" note.
  draw({});
  info.textContent = 'frames: 0\nwaiting for A2F blendshapes…\n(speak to the agent)';

  return { onFrame, onStart, onEnd };
}
