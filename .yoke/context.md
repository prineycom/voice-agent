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
A facial **emotion** of the **Avatar**, rendered from a Live2D `.exp3.json` file. Orthogonal
to **Motion state** — runs on a separate channel. Vocabulary is bounded by the expressions the
chosen model ships with.
_Avoid_: mood, face, emotion (reserve "emotion" for the source intent, see Emotion tag)

**Lip-sync**:
Volume-based mouth animation: the browser reads the RMS/peak of the **Avatar**'s incoming
WebRTC audio track and writes it to the `ParamMouthOpenY` model parameter each frame. NOT
phoneme/viseme-based and NOT computed on the TTS side. (Reverses the original "no lip-sync"
decision — see ADR.)
_Avoid_: mouth-sync, viseme animation

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
The Agent Worker parses it, strips it **before TTS and before the transcript**, and maps it to
an **Expression** in a **Motion event**. The allowed values are a fixed small enum
(`neutral | happy | sad | surprised | thinking`) — the single source of truth shared by SOUL.md
and the frontend; the frontend maps each enum value to a model `.exp3.json`, and any unknown tag
falls back to `neutral`.
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
