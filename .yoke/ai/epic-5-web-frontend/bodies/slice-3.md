## Parent

[Epic 5] Web Frontend — Live2D + Transcript + Tool Viz — https://github.com/prineycom/voice-agent/issues/5

## What to build

Make the Agent Worker the authoritative source of avatar motion and emotion (see ADR-0009). This is
a cross-component slice: Agent Worker + SOUL.md + frontend.

The Agent Worker publishes explicit **Motion events** (`{type: "motion", ...}`) on the existing
`voiceagent` **UI topic** alongside `tasks`/`event`, carrying both the **Motion state** and an
**Expression**. Emotion originates from **Emotion tags** the LLM emits inline in its reply (e.g.
`[emotion:happy]`); SOUL.md instructs the model to use them. The agent parses each tag out of the
streamed response and strips it **before TTS and before the transcript**, so it never reaches the
TTS engine or the displayed text, then maps it to an expression in the motion event.

Emotions are a fixed small enum — `neutral | happy | sad | surprised | thinking` — the single source
of truth shared by SOUL.md and the frontend. The frontend consumes motion events (taking precedence
over `lk.agent.state` via the slice-2 motion controller) and maps each enum value to a model
`.exp3.json`; any unknown tag falls back to `neutral`.

## Acceptance criteria

- [ ] Agent Worker publishes `{type:"motion"}` events on the `voiceagent` topic carrying motion state + expression
- [ ] SOUL.md instructs the LLM to emit inline emotion tags from the fixed enum
- [ ] The agent parses and strips emotion tags from the streamed response before TTS and before the transcript (tags never appear in spoken audio or chat)
- [ ] Emotion enum (`neutral|happy|sad|surprised|thinking`) is the shared contract; unknown → `neutral`
- [ ] Frontend applies motion events with precedence over `lk.agent.state` and maps each emotion to a `.exp3.json` expression
- [ ] A reply tagged with an emotion produces the matching avatar expression and the correct motion, with clean spoken audio and transcript
- [ ] Tests cover tag parsing/stripping in the Agent Worker

## Blocked by

- #27 — Live2D avatar + motion states (lk.agent.state)
