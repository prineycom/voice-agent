---
tags:
  - voice-agent
  - adr
status: accepted
supersedes: "no lip-sync decision in 0001/CLAUDE.md and Epic 5"
---

# Client-Side Volume-Based Lip-Sync (reverses "no lip-sync")

The original plan (Epic 5, CLAUDE.md) deliberately excluded lip-sync: "Live2D uses motion
states only (idle/listening/thinking/speaking), no lip-sync." We reverse that. The browser
already subscribes to the agent's audio as a LiveKit WebRTC track and already runs an
`AudioContext`/`AnalyserNode` for the mic VU meter, so reusing that pattern on the agent track
to drive the Live2D `ParamMouthOpenY` parameter each frame is ~20–30 lines and the mouth stays
in sync for free (we analyse the very audio that is playing).

## Considered Options

- **Client-side volume (RMS/peak) → `ParamMouthOpenY`** ✅ — cheap, reuses existing audio
  plumbing, naturally in sync, no TTS-side work. Crude (loudness, not articulation), but
  convincing enough for an avatar.
- **Phoneme/viseme lip-sync** ❌ — accurate articulation, but needs phoneme timing from TTS,
  per-model viseme mapping, and tight cross-machine sync. Large scope for marginal gain here.
- **No lip-sync (status quo)** ❌ — the original decision; rejected because the feature turned
  out to be nearly free and noticeably livens the avatar.

## Consequences

- Lip-sync is purely a frontend concern; the Desktop TTS service is untouched.
- It is independent of the **Motion state**: the `speaking` motion and the mouth movement are
  driven separately.
- CLAUDE.md and the Epic 5 text must be updated to drop the "no lip-sync" wording.
