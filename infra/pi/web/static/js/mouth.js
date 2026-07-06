// Mouth-opening controller: the SINGLE caller of avatar.setMouthOpen(). Owns
// provider selection (A2F blendshapes primary, volume analyser fallback), the
// A2F mouth-open shaping, an empty (D=0) delay-line seam for a mouth-only trim,
// and a cross-correlator instrument that measures the residual A2F↔audio offset
// at the end of each utterance. It does NOT touch lipsync.js: the existing volume
// analyser is reused unchanged by passing `mouth.volumeSink` into createLipSync()
// in place of `avatar` — volumeSink exposes the same setMouthOpen(v) sink shape.
//
// Playout-aligned buffering (ADR-0013 Phase 3) now lives one layer up, in
// schedule.js: frames are drained at `anchorWall + t·1000 + lagMs`, so
// ingestA2FFrame() below runs at scheduled apply time, not on wire arrival.
// The `a2fEnv` timestamps therefore already trail the wire by that constant
// lag — see crossCorrelateOffset()'s doc for what its output measures now.
//
// Only one provider drives the mouth at a time. When A2F is streaming it owns the
// mouth; volume frames are still recorded (for correlation) but not forwarded. On
// endA2FStream() the mouth reverts to volume on the next volumeSink tick.

// Bounded ring of {t, ...} samples spanning ~windowMs of wall-clock time; drops
// stale entries on push so it never grows unbounded during a long utterance.
function makeRing(windowMs) {
  const buf = [];
  return {
    push(sample) {
      buf.push(sample);
      const cutoff = sample.t - windowMs;
      while (buf.length && buf[0].t < cutoff) buf.shift();
    },
    clear() { buf.length = 0; },
    get items() { return buf; },
  };
}

export function a2fMouthOpen(arkit) {
  // MouthFunnel is reserved for future shaping (Phase 3); not yet read.
  const g = k => arkit[k] || 0;
  return clamp01(g('JawOpen') * (1 - g('MouthClose')));
}

export function crossCorrelateOffset(a, b) {
  // Resample both series onto a common 10ms grid over their overlapping span,
  // subtract means, then find the integer lag maximising the normalised
  // cross-correlation. Positive lag = `a` leads `b` (A2F earlier than audio).
  // Callers now feed `a2fEnv` samples stamped at schedule.js's scheduled apply
  // time (anchor + t·1000 + lagMs), not wire arrival, so the returned offset
  // already has that constant lag baked in — it measures residual playout
  // misalignment, useful for tuning schedule.js's lagMs, not raw A2F latency.
  const gridMs = 10;
  const maxLagMs = 300;
  if (!a || !b || a.length < 2 || b.length < 2) return 0;
  const lo = Math.max(a[0].t, b[0].t);
  const hi = Math.min(a[a.length - 1].t, b[b.length - 1].t);
  const span = hi - lo;
  if (span < gridMs * 4) return 0;              // overlap too small to correlate
  const n = Math.floor(span / gridMs) + 1;
  const ga = resample(a, lo, gridMs, n);
  const gb = resample(b, lo, gridMs, n);
  demean(ga);
  demean(gb);
  const maxLag = Math.floor(maxLagMs / gridMs);
  // Require a healthy overlap per lag so a large shift with only a few surviving
  // samples can't score spuriously high and bias the reported offset.
  const minOverlap = Math.max(8, Math.floor(n / 2));
  let bestLag = 0;
  let bestScore = -Infinity;
  for (let lag = -maxLag; lag <= maxLag; lag++) {
    let dot = 0, ea = 0, eb = 0, count = 0;
    for (let i = 0; i < n; i++) {
      const j = i + lag;
      if (j < 0 || j >= n) continue;
      const x = ga[i], y = gb[j];
      dot += x * y; ea += x * x; eb += y * y; count++;
    }
    if (count < minOverlap || ea === 0 || eb === 0) continue;
    const score = dot / Math.sqrt(ea * eb);     // normalised so lags compare fairly
    if (score > bestScore) { bestScore = score; bestLag = lag; }
  }
  // b shifted by +lag samples aligns with a → a leads b by lag*gridMs ms.
  return bestLag * gridMs;
}

// Linear-interpolate a {t, v} series onto n points starting at t0, step gridMs.
function resample(series, t0, gridMs, n) {
  const out = new Float32Array(n);
  let idx = 0;
  for (let i = 0; i < n; i++) {
    const t = t0 + i * gridMs;
    while (idx < series.length - 1 && series[idx + 1].t <= t) idx++;
    const p = series[idx];
    const q = series[idx + 1] || p;
    const dt = q.t - p.t;
    out[i] = dt > 0 ? p.v + (q.v - p.v) * ((t - p.t) / dt) : p.v;
  }
  return out;
}

function demean(arr) {
  let sum = 0;
  for (let i = 0; i < arr.length; i++) sum += arr[i];
  const mean = sum / arr.length;
  for (let i = 0; i < arr.length; i++) arr[i] -= mean;
}

function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }

export function createMouth(avatar, { log, forceVolume = false, now } = {}) {
  now = now || (() => performance.now());
  const volEnv = makeRing(2000);   // ~2s of volume-mouth targets, for correlation
  const a2fEnv = makeRing(2000);   // ~2s of A2F mouth-open values, for correlation
  let a2fActive = false;
  let cur = 0;                     // smoothed A2F mouth-open (30fps → 60fps lerp)

  const provider = () => (a2fActive && !forceVolume) ? 'a2f' : 'volume';

  const delayMs = 0; // Mouth-only trim knob, separate from schedule.js's playout-aligned
                     // buffering (ADR-0013 Phase 3, which now owns when ingestA2FFrame()
                     // below runs). Kept D=0 here — a genuine seam, not a second scheduler.
  function applyA2F(value) {
    // delayMs === 0 → pass-through. A future fine-trim can slot in here keyed on pts.
    if (provider() === 'a2f') avatar.setMouthOpen(value);
  }

  const volumeSink = {
    setMouthOpen(v) {
      // Always record for correlation; only forward when volume owns the mouth.
      volEnv.push({ t: now(), v });
      if (provider() === 'volume') avatar.setMouthOpen(v);
    },
  };

  function beginA2FStream() {
    a2fActive = true;
    cur = 0;
    volEnv.clear();
    a2fEnv.clear();
  }

  function ingestA2FFrame(frame) {
    const open = a2fMouthOpen(frame.arkit);
    cur += (open - cur) * 0.5;                  // smooth 30fps source toward 60fps render
    a2fEnv.push({ t: now(), pts: frame.t, v: open });
    applyA2F(cur);
  }

  function endA2FStream() {
    const a = a2fEnv.items;
    const b = volEnv.items;
    // Guard tiny windows — a correlation over a handful of samples is noise.
    if (a.length >= 8 && b.length >= 8) {
      const off = crossCorrelateOffset(a, b);
      if (log) log('mouth sync offset: ' + Math.round(off) + 'ms');
    }
    a2fActive = false;   // next volumeSink tick reclaims the mouth
  }

  return { volumeSink, beginA2FStream, ingestA2FFrame, endA2FStream };
}
