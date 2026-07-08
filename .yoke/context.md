# Voice Agent — Glossary

Project-specific terminology for the Voice Agent platform. Captures the language used when
talking about the frontend, avatar, and the agent↔browser event protocol.

## Language

### Frontend & avatar

**Avatar**:
The Live2D character rendered in the browser via `pixi-live2d-display` + PIXI. Driven by
motion states, expressions, and lip-sync. Uses a free Cubism sample model that ships with
`.exp3.json` expression files.
_Avoid_: model (ambiguous with LLM model), character, sprite

**Motion state**:
One of exactly four whole-body animation states of the **Avatar**: `idle`, `listening`,
`thinking`, `speaking`. Maps to a Live2D motion group. Authoritative source is an explicit
**Motion event** from the Agent Worker (not `lk.agent.state`).
_Avoid_: status, mode, animation

**Expression**:
A facial **emotion** of the **Avatar**. Historically rendered from a Live2D `.exp3.json` file
driven by an **Emotion tag**. Under **Facial animation** (Epic 8) this `.exp3.json` path is
**replaced**: facial emotion is produced by **Audio2Face** from its **Emotion vector** input.
Per ADR-0016 (landed in #40) the baseline vector comes from **Audio2Emotion**, with the
**Emotion tag** as an additive boost — a `neutral`/zero tag means pure A2E. Still orthogonal
to **Motion state**.
_Avoid_: mood, face, emotion (reserve "emotion" for the source intent, see Emotion tag)

**Lip-sync**:
Mouth-opening animation that writes `ParamMouthOpenY` each frame. It has two interchangeable
providers behind one sink (`avatar.setMouthOpen`): the **A2F provider** (primary, Epic 8) drives
the mouth from **Audio2Face**'s phoneme-level `JawOpen` **Blendshape**; the **volume provider**
(fallback + toggle) reads the RMS/peak of the **Avatar**'s incoming WebRTC audio track (the
original ADR-0008 path, which reverses the "no lip-sync" decision). The volume provider is the
automatic fallback when no A2F stream is active and a runtime toggle to force the old behaviour.
See ADR-0013 (reverses ADR-0012 decision #1). NOT computed on the TTS side.
_Avoid_: mouth-sync, viseme animation

**Facial animation**:
The Epic-8 enhancement where **Audio2Face** drives the **Avatar**'s face from TTS audio: blink,
gaze, brow, mouth *form*, **and** (per ADR-0013) mouth *opening* via the A2F **Lip-sync** provider.
The volume **Lip-sync** provider remains the fallback/toggle for mouth opening. Distinct from
**Expression** (emotion-tag `.exp3.json`, now replaced by A2F) and **Motion state** (whole-body).
_Avoid_: face tracking

**Blendshape**:
One of the 52 ARKit face weights (0.0–1.0, e.g. `jawOpen`, `eyeBlinkLeft`, `browInnerUp`) that
**Audio2Face** emits per frame from TTS audio. Mapped to Live2D standard parameters by
`arkitToLive2D()`. The unit of the **Facial animation** data stream.
_Avoid_: viseme, morph target

**Audio2Face** (A2F):
NVIDIA Audio2Face-3D running on the Desktop GPU; consumes TTS PCM audio plus an **Emotion
vector** and emits **Blendshape** frames (~30 FPS) that drive **Facial animation**. Face-only —
never head/body/hands (those stay on **Motion state**).
_Avoid_: A2F-2D, facial capture

**Audio2Emotion** (A2E):
The emotion-inference half of the A2F stack: infers an **Emotion vector** from the audio's
prosody. The full NVIDIA NIM always blends it in; since #40 (ADR-0016) our slim helper runs it
per utterance as the baseline emotion source, with the **Emotion tag** as an additive boost via
the SDK preferred-emotion channel. `A2E_ENABLED=0` rolls the helper back to tag-only.
_Avoid_: sentiment analysis, emotion recognition (too generic)

**Emotion vector**:
The 10-dimensional A2E-vocabulary input to **Audio2Face**
(`grief, joy, disgust, outofbreath, pain, anger, amazement, cheekiness, sadness, fear`,
each 0.0–1.0) that colors the emitted **Blendshapes**. Since #40 the baseline is inferred per
utterance by **Audio2Emotion** from the audio; the sparse **Emotion tag** vector is an additive
boost on top (all-zeros/`neutral` tag ⇒ pure A2E). The wire format (`/tts` field → A2F fork →
helper stdin) is unchanged.
_Avoid_: emotion state, mood vector

**Kiosk**:
The frontend running fullscreen (1080p) in Chromium on the Pi 5. Same responsive codebase as
**Web** — not a separate app.
_Avoid_: display mode

**Web**:
The same frontend accessed from a desktop/mobile browser on the LAN/tailnet. Shares all code
with **Kiosk**.

**Test harness**:
The current `infra/pi/web/index.html` dev page used to validate transport (LiveKit, transcript,
tools, mic, VU). Epic 5 builds the real frontend from it; it stays as a developer tool.

### Agent↔browser protocol

**UI topic**:
The LiveKit data-channel topic `voiceagent` the browser subscribes to. Carries `tasks`,
`event` (tool feed), and the new `motion`/expression messages.

**Motion event**:
A message the Agent Worker publishes on the **UI topic** to authoritatively set the
**Motion state** and **Expression** of the **Avatar**.

**Emotion tag**:
An inline marker the LLM emits in its response (e.g. `[emotion:happy]`) to express intent.
The Agent Worker parses it and strips it **before TTS and before the transcript**. Historically
it mapped to an **Expression** (`.exp3.json`) in a **Motion event**; under **Facial animation**
(Epic 8) the parsed emotion instead feeds **Audio2Face**'s emotion input — since #40 as an
additive *boost* on the **Audio2Emotion** baseline (SDK preferred-emotion channel, enabled only
for a non-zero tag), not as the sole source. The allowed values are a fixed small enum
(`neutral | happy | sad | surprised | thinking`), the single source of truth shared by SOUL.md,
the agent, and (via A2F) the avatar; any unknown tag falls back to `neutral`.
_Avoid_: sentiment, emotion marker

## Example dialogue

> **Dev:** When the agent starts answering, the avatar should open its mouth, right?
> **Domain:** Two separate things. The *motion state* flips to `speaking` from a motion event.
> The mouth movement is *lip-sync* — driven by Audio2Face's phoneme-level `JawOpen`, with the
> client-side volume analyser kept as fallback/toggle. Either way it is independent of the
> motion state.
> **Dev:** And if the model "smiles"?
> **Domain:** That's an *expression*. The LLM tagged its reply with an *emotion tag* like
> `[emotion:happy]`; the agent strips the tag before TTS and it becomes part of the *emotion
> vector* fed to Audio2Face, which shapes the emitted blendshapes. No `.exp3.json` is involved
> anymore — and since ADR-0016 landed (#40), *Audio2Emotion* infers the baseline vector from
> the voice itself, the tag only boosting it.
