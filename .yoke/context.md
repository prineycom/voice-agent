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
**replaced**: facial emotion is produced by **Audio2Face** (from audio prosody plus the
**Emotion tag** fed into A2F's emotion input), not by expression files. Still orthogonal to
**Motion state**.
_Avoid_: mood, face, emotion (reserve "emotion" for the source intent, see Emotion tag)

**Lip-sync**:
Volume-based mouth animation: the browser reads the RMS/peak of the **Avatar**'s incoming
WebRTC audio track and writes it to the `ParamMouthOpenY` model parameter each frame. NOT
phoneme/viseme-based and NOT computed on the TTS side. (Reverses the original "no lip-sync"
decision — see ADR.) Under **Facial animation** (Epic 8) the mouth *amplitude* stays on this
local volume path — it is the audio-synchronised layer that A2F does not replace.
_Avoid_: mouth-sync, viseme animation

**Facial animation**:
The Epic-8 enhancement that adds **Audio2Face** blink, gaze, brow, and mouth-*form* on top of
the volume-based **Lip-sync**. A *hybrid*: mouth opening amplitude (`ParamMouthOpenY`) stays on
the audio-synced volume path; A2F drives only the loose-sync facial parameters. Distinct from
**Expression** (emotion-tag `.exp3.json`) and **Motion state** (whole-body).
_Avoid_: face tracking, lip-sync (reserve "lip-sync" for the mouth-amplitude layer)

**Blendshape**:
One of the 52 ARKit face weights (0.0–1.0, e.g. `jawOpen`, `eyeBlinkLeft`, `browInnerUp`) that
**Audio2Face** emits per frame from TTS audio. Mapped to Live2D standard parameters by
`arkitToLive2D()`. The unit of the **Facial animation** data stream.
_Avoid_: viseme, morph target

**Audio2Face** (A2F):
NVIDIA Audio2Face-3D running on the Desktop GPU; consumes TTS PCM audio and emits **Blendshape**
frames (~30 FPS) that drive **Facial animation**. Face-only — never head/body/hands (those stay
on **Motion state**).
_Avoid_: A2F-2D, facial capture

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
(Epic 8) the parsed emotion instead feeds **Audio2Face**'s emotion input so the same enum shapes
A2F's facial output. The allowed values are a fixed small enum
(`neutral | happy | sad | surprised | thinking`), the single source of truth shared by SOUL.md,
the agent, and (via A2F) the avatar; any unknown tag falls back to `neutral`.
_Avoid_: sentiment, emotion marker

## Example dialogue

> **Dev:** When the agent starts answering, the avatar should open its mouth, right?
> **Domain:** Two separate things. The *motion state* flips to `speaking` from a motion event.
> The mouth movement is *lip-sync* — driven client-side off the audio volume, independent of
> the motion state.
> **Dev:** And if the model "smiles"?
> **Domain:** That's an *expression*. The LLM tagged its reply with an *emotion tag* like
> `[emotion:happy]`; the agent stripped the tag before TTS and sent a *motion event* carrying
> the expression. The browser plays the matching `.exp3.json`.
