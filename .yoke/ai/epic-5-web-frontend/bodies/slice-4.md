## Parent

[Epic 5] Web Frontend — Live2D + Transcript + Tool Viz — https://github.com/prineycom/voice-agent/issues/5

## What to build

Add **Lip-sync** to the **Avatar** (see ADR-0008, which reverses the original "no lip-sync"
decision). This is a frontend-only slice. The browser already subscribes to the agent's audio as a
LiveKit WebRTC track and already runs an `AudioContext`/`AnalyserNode` for the mic VU meter; reuse
that pattern on the agent's audio track instead of the mic, compute the per-frame volume (RMS/peak),
and write it to the Live2D `ParamMouthOpenY` model parameter each animation frame.

Because we analyse the exact audio being played, the mouth stays in sync for free. This is
volume-based, not phoneme/viseme articulation, and the Desktop TTS service is untouched. Lip-sync
runs independently of the `speaking` **Motion state** (both can be active at once).

## Acceptance criteria

- [ ] The agent's WebRTC audio track is analysed (RMS/peak) per animation frame in the browser
- [ ] `ParamMouthOpenY` is driven from that volume so the avatar's mouth moves while the agent speaks
- [ ] Mouth movement is visibly in sync with the spoken audio
- [ ] Mouth returns to closed/neutral when the agent is silent
- [ ] No changes to the Desktop TTS service; lip-sync coexists with the `speaking` motion and expressions

## Blocked by

- #28 — Agent-authoritative motion events + LLM emotion tags
