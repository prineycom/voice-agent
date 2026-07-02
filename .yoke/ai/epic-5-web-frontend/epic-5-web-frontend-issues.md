# Epic 5 — Web Frontend: issue breakdown

Parent: [Epic 5] Web Frontend — Live2D + Transcript + Tool Viz — https://github.com/prineycom/voice-agent/issues/5

Derived from grilling (see `.yoke/context.md` and `docs/adr/0008`–`0011`). Five vertical slices,
all AFK (`ready-for-agent`), linked as sub-issues of #5. Kiosk deferred to Epic 6.

| # | Slice | Type | Blocked by | Issue |
|---|-------|------|-----------|-------|
| 1 | Frontend: vanilla ES modules + new layout | AFK | — | https://github.com/prineycom/voice-agent/issues/26 |
| 2 | Live2D avatar + motion states (lk.agent.state) | AFK | #26 | https://github.com/prineycom/voice-agent/issues/27 |
| 3 | Agent-authoritative motion events + LLM emotion tags | AFK | #27 | https://github.com/prineycom/voice-agent/issues/28 |
| 4 | Volume-based lip-sync (ParamMouthOpenY) | AFK | #28 | https://github.com/prineycom/voice-agent/issues/29 |
| 5 | Responsive desktop+mobile + polish | AFK | #27 | https://github.com/prineycom/voice-agent/issues/30 |

Slice bodies: `.yoke/ai/epic-5-web-frontend/bodies/slice-{1..5}.md`

## Relevant ADRs
- `docs/adr/0008-client-volume-lipsync.md` — lip-sync reversal (slice 4)
- `docs/adr/0009-agent-authoritative-motion-emotion.md` — motion/emotion protocol (slice 3)
- `docs/adr/0010-live2d-pixi-cubism-core.md` — Live2D stack (slice 2)
- `docs/adr/0011-frontend-vanilla-es-modules.md` — vanilla over React (slice 1)
