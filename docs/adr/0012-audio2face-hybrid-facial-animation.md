---
tags:
  - voice-agent
  - adr
status: accepted
relates-to: "0008-client-volume-lipsync, 0009-agent-authoritative-motion-emotion"
---

# Hybrid Audio2Face-3D Facial Animation (keeps volume lip-sync, replaces expressions)

Epic 8 adds NVIDIA Audio2Face-3D (A2F) to drive phoneme/prosody-level facial animation on the
Live2D **Avatar** from TTS audio. The naive reading of the epic — send all 52 ARKit blendshapes
to the browser and drive the whole face, including the mouth — breaks the one property the
current lip-sync gets for free: **sync**. Today the mouth is driven by the browser analysing the
very WebRTC audio track it is playing (ADR-0008), so it is perfectly aligned. A2F computes
blendshapes on the Desktop from PCM *before* transmission and delivers them over a *different*
path (data channel) than the audio (WebRTC, jitter-buffered). The two streams have independent,
variable latency, so an A2F-driven mouth would visibly lead or lag the sound.

We therefore adopt a **hybrid** split and let A2F **replace** the emotion-tag expression system.

## Decision

- **Mouth amplitude stays local.** `ParamMouthOpenY` remains driven by the client-side volume
  analyser (ADR-0008) — the audio-synchronised layer. A2F does **not** drive mouth opening.
- **A2F drives the loose-sync face** — blink, gaze, brows, eye-squint, mouth *form* — parameters
  where small timing drift is imperceptible.
- **A2F replaces the `.exp3.json` expression system.** Facial emotion now comes only from A2F
  (audio prosody + emotion input). The old **Emotion tag** → `.exp3.json` frontend path is
  deleted (reverses the `.exp3.json` half of ADR-0009; motion states are unchanged).
- **The Emotion tag feeds A2F's emotion input**, not expression files. It is carried as an
  `emotion` field on each per-sentence `/tts` request so it stays bound to the exact audio A2F
  processes.
- **Transport is the existing `voiceagent` data channel via the agent.** A2F runs as a separate
  Desktop service; the TTS service forks its PCM locally into A2F; the agent forwards blendshapes
  onto the data channel. The browser always reaches the Pi; it does not always reach the Desktop.
- **Idle face releases to Live2D's built-in auto-blink.** A2F emits only while the agent speaks;
  between turns the frontend stops pinning eye/brow params so the model's own `EyeBlink`/idle
  motion runs.
- **Fallback = local mouth + neutral face.** If A2F is down (crashed, spike not ready, Desktop
  asleep) the mouth still moves; the rest of the face stays neutral. No `.exp3.json` fallback.
- **Deployment is decided by a feasibility spike** (native-Python service under NSSM vs the
  Docker/gRPC NIM) before committing, since the Desktop is Windows with no Docker today.

## Considered Options

- **Hybrid: local mouth + A2F loose-sync face** ✅ — sidesteps the hardest cross-transport sync
  problem entirely (the mouth, which most needs sync, never leaves the audio-synced path) while
  gaining A2F's blink/gaze/brow/emotion. A2F output for parameters a model lacks silently no-ops.
  Both in-repo models (Hiyori, Natori) already expose the full necessary standard set (blink,
  gaze, `ParamBrowLAngle`/`RAngle`, eye-squint, `ParamMouthForm`, `ParamCheek`); driving params
  directly means Hiyori's lack of `.exp3.json` expression *files* no longer matters.
- **Full A2F with timestamped blendshapes buffered against audio playout** ❌ — accurate mouth
  sync, but needs a shared clock/PTS across the WebRTC audio and the data-channel blendshapes and
  browser-side buffering. Large scope for a mouth we already animate acceptably.
- **Full A2F applied on arrival** ❌ — simplest, but the mouth visibly leads/lags the sound;
  regresses the sync ADR-0008 bought for free.

## Consequences

- The mouth-open path and the sync guarantee of ADR-0008 are preserved untouched.
- The `.exp3.json` expression pipeline (`EmotionTagStripper` → motion-event `expression` →
  `model.expression()`) is removed on the frontend; the agent keeps parsing emotion tags but
  routes them to A2F via the `/tts` request instead. CLAUDE.md's expression wording needs an
  update.
- A2F is a hard dependency for blink/gaze/brow/emotion but **not** for the mouth, so a Desktop
  outage degrades gracefully rather than freezing the avatar.
- Requires verifying the chosen A2F-3D build accepts an emotion-input vector; if it does not, the
  emotion enum has no effect and facial emotion is prosody-only until revisited.
- The full *necessary* facial set is already covered by both in-repo models (Hiyori active,
  Natori richer), all under a non-commercial license that suits this project. No new model is
  required. Only truly-extended params (`ParamTongue`, `ParamPuffCheeks`, `ParamMouthX`,
  `ParamMouthSize`) are absent from every standard rig — they need a paid Perfect-Sync model and
  A2F no-ops on them, so they are out of scope.
