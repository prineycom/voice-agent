---
tags:
  - voice-agent
  - adr
status: accepted
supersedes: "0012 decision #1 (mouth amplitude stays on the local volume analyser)"
relates-to: "0008-client-volume-lipsync, 0012-audio2face-hybrid-facial-animation"
---

# Audio2Face-Driven Lip-Sync with Volume Fallback (reverses ADR-0012 decision #1)

ADR-0012 kept mouth opening (`ParamMouthOpenY`) on the client-side volume analyser (ADR-0008) and
let **Audio2Face** (A2F) drive only the loose-sync face (blink, gaze, brows, mouth *form*). That
choice bought perfect mouth sync for free by never taking the mouth off the audio-synchronised
path, at the cost of articulation: the mouth opens with **loudness**, not with phonemes.

We now reverse decision #1. The mouth should articulate — A2F already emits a phoneme-level
`JawOpen` (plus `MouthClose`/`MouthFunnel` shaping) per frame that we currently discard
(`infra/desktop/a2f/arkit.py`). We make A2F the **primary** driver of mouth opening and keep the
volume analyser as a runtime-selectable **fallback and toggle**, so the old behaviour is never lost
and remains a one-flag retreat. The rest of ADR-0012 stands unchanged.

## Decision

- **Mouth-opening source is pluggable** behind the single existing sink
  `avatar.setMouthOpen(0..1)` (`infra/pi/web/static/js/avatar.js`). Two providers implement one
  interface:
  - **`A2FLipSync` (primary)** — drives `ParamMouthOpenY` from the A2F `JawOpen` coefficient (with
    `MouthClose`/`MouthFunnel` shaping), riding the same blendshape stream as the loose-sync face.
  - **`VolumeLipSync` (fallback + toggle)** — the existing ADR-0008 analyser (`lipsync.js`),
    retained verbatim. It is the **automatic fallback** whenever no A2F blendshape stream is active,
    and a **runtime toggle** (config flag / URL param) to force the old behaviour at will.
- **Sync is the core sub-problem**, not a detail — it is exactly what ADR-0012 avoided. The A2F path
  (Desktop → `/a2f` WS → agent → `voiceagent` DataChannel) has independent, typically *lower*
  latency than the jitter-buffered WebRTC audio, so a mouth applied on arrival tends to **lead** the
  sound. To make alignment possible without re-cutting the wire later, each blendshape frame carries
  a **presentation timestamp (PTS)** relative to its utterance/audio. Rollout is measure-first:
  1. Ship A2F mouth **apply-on-arrival** and measure perceived lead/lag against the audio.
  2. If drift is objectionable, enable **playout-aligned buffering** on the browser — hold blendshape
     frames and apply each at its PTS against the audio playout position.
- **Fallback semantics change.** A2F down/absent → the mouth falls back to **`VolumeLipSync`** (it
  still moves), while the rest of the face degrades to neutral (per ADR-0012). The previous
  "neutral mouth on A2F outage" no longer applies — a moving mouth is the graceful state.
- **The worst case degrades to ADR-0012.** If browser-side playout alignment proves impractical
  (no reliable PTS on a remote WebRTC track), we keep A2F for mouth *form* and route mouth *opening*
  back to `VolumeLipSync` — i.e. we retreat exactly to the ADR-0012 hybrid. The toggle is that
  safety net made explicit.
- **Everything else in ADR-0012 is unchanged:** A2F drives the loose-sync face, replaces the
  `.exp3.json` expression system, rides the `voiceagent` DataChannel via the agent, releases
  eye/brow params to Live2D auto-blink when idle, and takes the **Emotion tag** through the `/tts`
  `emotion` field.

## Considered Options

- **A2F mouth, pluggable, PTS in the contract from day one, measure-then-align** ✅ — gets phoneme
  articulation, keeps the volume path as a first-class fallback/toggle (no regression risk), and
  bakes the one field (PTS) that any accurate-sync approach needs so we don't rewrite the protocol
  twice. Lets us ship the cheap version first and only pay for buffering if measurement demands it.
- **A2F mouth applied on arrival, no PTS** ❌ as the end state — simplest, but with no timestamp we
  can never correct the lead/lag; acceptable only as the transient step-1 above, not the contract.
- **Volume-only (ADR-0012 status quo)** ❌ as primary — perfect sync but cannot articulate; retained
  as the fallback/toggle rather than dropped.
- **Full timestamped buffering from the start** ❌ for now — the accurate target, but front-loads a
  shared-clock/PTS + browser-buffer build before we know the naive drift is even objectionable.
  Deferred behind the measurement gate, not rejected.

## Consequences

- `infra/pi/web/static/js/lipsync.js` becomes one of two mouth providers behind a source selector;
  **nothing is deleted** — the volume path survives as fallback and toggle.
- A2F becomes a soft dependency for mouth *quality* but not for a *moving* mouth (the fallback covers
  outages), so degradation on a Desktop outage is actually **better** than ADR-0012's neutral mouth.
- The blendshape wire (A2F service → agent → DataChannel) gains a per-frame **PTS** field; the agent
  forwards it; the browser may buffer against audio playout. This lightly extends Epic 8 Phase 3.
- `JawOpen` is now consumed on the client — the "intentionally NOT used" note in
  `infra/desktop/a2f/arkit.py` and the ADR-0012 wording become stale and are updated.
- **Epic 8 Phase 2 is reshaped:** it now delivers a mouth-source selector (A2F primary + volume
  fallback/toggle) and the mouth-sync sub-task, instead of "leave `ParamMouthOpenY` on the volume
  analyser."
- Risk: browser access to a stable audio-playout clock for a remote WebRTC track is unproven; the
  toggle/fallback design bounds this risk to "retreat to the ADR-0012 hybrid," never a broken mouth.
