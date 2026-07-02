---
tags:
  - voice-agent
  - adr
status: accepted
---

# Agent Is the Authoritative Source of Avatar Motion and Emotion

The Live2D avatar's motion state (idle/listening/thinking/speaking) and facial expression are
driven by explicit **Motion events** the Agent Worker publishes on the existing `voiceagent`
data-channel topic — not by the browser inferring state from `lk.agent.state`. Emotion comes
from **Emotion tags** the LLM emits inline in its reply (e.g. `[emotion:happy]`), which the
agent parses and strips before TTS and before the transcript, then maps to an expression in the
motion event. This makes Epic 5 cross-component (frontend + Agent Worker + SOUL.md).

## Considered Options

- **Agent emits explicit motion + emotion events** ✅ — one authoritative source; lets the
  agent express emotion (which the browser cannot infer) and keeps richer future states open.
- **Frontend drives motion from `lk.agent.state`** ❌ — zero agent changes, but limited to the
  four built-in states and carries no emotion; the browser would be guessing intent.

## Consequences

- The `voiceagent` topic gains a `motion` message type alongside `tasks`/`event`.
- Emotions are a fixed small enum (`neutral | happy | sad | surprised | thinking`), the single
  source of truth shared by SOUL.md and the frontend; the frontend maps enum → `.exp3.json`,
  unknown → `neutral`.
- The agent's LLM→TTS pipeline must parse and strip emotion tags from the streamed response so
  they never reach the TTS engine or the transcript.
