---
tags:
  - voice-agent
  - adr
status: accepted
---

# Wake-Word Activation: Server-Side livekit-wakeword Gate on the Pi

The agent gains a **wake-word activation** layer on both frontends (Live2D `index.html`
and 3D `face3d.html`). The agent starts **Dormant** — user speech is ignored until a
wake word (`Приней`, `Приня`, `хей джарвис`) moves it to **Active**, where speech is
processed normally. It returns to Dormant on an 8 s silence timeout or a `{wake} + стоп`
Stop phrase. See `.yoke/context.md` for the term definitions.

Detection runs **server-side, in the Agent Worker on the Pi**, using the
[`livekit-wakeword`](https://github.com/livekit/livekit-wakeword) ONNX classifier scoring
the user's incoming WebRTC audio frames — a lightweight always-on gate in front of the heavy
Desktop GPU STT. It does **not** run in the browser, even though that is livekit-wakeword's
official (client-side) deployment pattern and even though the microphone lives in the browser.

## Context

- Today the mic streams to LiveKit unconditionally on Connect; the only control is Mute.
  The Desktop `faster-whisper` STT already transcribes every VAD-endpointed turn, so *any*
  nearby speech pays the full GPU cost even when not addressed to the agent. The user's
  driving concern was this wasted GPU/backend load.
- The wake words are **custom Russian names**. Off-the-shelf browser hotword engines either
  can't do arbitrary Russian words without paid keyword training (Porcupine) or send audio
  to the cloud (Web Speech API) — undermining the local/private premise.
- `livekit-wakeword` provides a one-command custom-keyword **training pipeline** (synthetic
  TTS data via VoxCPM2 — already stood up on the Desktop — + augmentation → tiny CNN → ONNX
  export), supports Russian (with a noted lower non-English accuracy), and is the same vendor
  as our existing transport. **But its client SDKs are Python / Rust / Swift only — there is
  no JS/WASM SDK**, and our frontend is vanilla-JS-in-browser (ADR-0011).

## Decision

Run the trained `livekit-wakeword` ONNX classifier as a **lightweight CPU gate inside the
Agent Worker on the Pi**, scoring the user's WebRTC frames. Only on a wake-word hit does the
turn reach the Desktop GPU STT → LLM → TTS pipeline. The wake word is stripped from the
transcript, and a one-breath `{wake}, <request>` is answered in the same turn. Dormant/Active
is a single **room-global** state. The Pi publishes the transition over the existing
`voiceagent` UI topic so the browser can play the **activation signal** (chime + avatar
reaction) — symmetric quieter signal on sleep.

## Considered Options

- **Server-side livekit-wakeword gate on the Pi** ✅ — solves custom-Russian words via the
  training pipeline; keeps the heavy GPU STT gated (addresses the load concern); same vendor;
  one central place that serves Web and Kiosk identically with **no frontend hotword code**.
  Cost: audio still streams to the Pi continuously and the Pi CPU runs a (cheap) classifier;
  detection is not truly on-device.
- **Browser hotword engine (Vosk-browser / Porcupine WASM)** ❌ — would keep the mic
  unpublished until activation (lowest backend load, most private), but custom Russian names
  are the exact weak spot: Vosk short-name false-positives need tuning; Porcupine needs paid
  keyword training + an AccessKey. Different stack, per-client, extra kiosk wiring.
- **Rust→WASM build of livekit-wakeword for the browser** ❌ — would give both the vendor
  engine *and* browser placement, but it is an unofficial build path with significant
  effort/risk and no supported binding.
- **Full-STT + text match (no dedicated detector)** ❌ — rejected: runs the heavy GPU STT on
  every utterance, which is the wasteful behavior we set out to remove.

## Consequences

- The wake-word gate sits **in front of** the LiveKit turn pipeline (ADR-0006 still owns
  VAD/endpointing/interruption once Active). It is a new input gate, which the framework does
  not currently expose — the insertion mechanism (frame tap / gating STT dispatch) is an
  implementation detail to resolve during build.
- Because detection is server-side, the "audio never leaves the machine until activation"
  benefit is **not** obtained — this is the accepted trade for reliable custom-RU detection.
- Both frontends get the feature for free (server-side, room-global); only the signal
  rendering (chime + avatar motion/expression on `voiceagent`) is new client work.
- A **master config flag** (default ON) in the agent `.env` gates the whole feature; OFF
  restores today's always-listening behavior. A separate **follow-up** flag (default ON)
  toggles the Active conversation window vs a strict "wake word every turn" mode.
- The greeting (`AGENT_GREETING`) is removed — the agent is silent until first woken.
- Multilingual accuracy caveat: `Приней`/`Приня` are short and non-English; false-accept /
  false-reject thresholds will need tuning, and may motivate revisiting the browser or hybrid
  option if server-side accuracy proves insufficient.
