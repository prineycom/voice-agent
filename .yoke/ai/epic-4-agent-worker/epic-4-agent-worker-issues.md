# Epic 4 — Agent Worker on Pi 5: Issue Breakdown

Parent epic: [Epic 4] Agent Worker on Pi 5 — https://github.com/prineycom/voice-agent/issues/4

Vertical tracer-bullet slices, split along integration-risk boundaries:
output path → input path → LLM brain → barge-in → tools.
Dependency order: 1 → 2 → 3 → {4, 5}.

Related docs: `CONTEXT.md` (glossary), `docs/adr/0003-websocket-stt-tts.md`,
`docs/adr/0004-llm-model.md`, `docs/adr/0006-agent-turn-control.md`.

Design decisions behind these slices were resolved in a grilling session
(`/yoke:grill-docs` on #4): AgentSession over a hand-rolled loop; LiveKit owns
endpointing + interruption; close-socket TTS abort; sentence chunking; Russian-only
MVP; LiteLLM proxy + openai plugin; Ollama Cloud API key; venv + systemd; local
SOUL.md; tools deferred; Hermes over HTTP; framework-native resampling;
health-gating; mocked unit tests + manual e2e smoke.

---

## Slice 1 — Agent Worker scaffold + TTS greeting (output audio path)

- **Type:** AFK (`ready-for-agent`)
- **Blocked by:** Epic 1 (#1, SFU), Epic 3 (TTS service)
- **Issue:** https://github.com/prineycom/voice-agent/issues/11
- **Sub-issue of:** #4 ✅

Worker on Pi (venv + systemd) registers with the SFU, joins a room, and speaks a
canned Russian greeting via the custom TTS plugin (`/tts` WS, 24kHz→LiveKit
resample). Health-gates TTS. Proves deployment, worker registration, room join,
TTS plugin, output audio path.

---

## Slice 2 — STT echo loop (input path + LiveKit turn detection)

- **Type:** AFK (`ready-for-agent`)
- **Blocked by:** #11 (Slice 1), Epic 2 (STT service)
- **Issue:** https://github.com/prineycom/voice-agent/issues/12
- **Sub-issue of:** #4 ✅

Adds the custom STT plugin (`/stt` WS) + AgentSession VAD/turn detection. Agent
transcribes Russian speech and speaks it back (echo, no LLM). Proves mic capture,
16kHz resample, LiveKit-owned endpointing (`{"event":"end"}` on turn-end),
per-session STT socket, full duplex turn-taking. Realizes ADR-0006 (endpointing).

---

## Slice 3 — LiteLLM proxy + real conversation with SOUL personality

- **Type:** AFK (`ready-for-agent`)
- **Blocked by:** #12 (Slice 2)
- **Issue:** https://github.com/prineycom/voice-agent/issues/13
- **Sub-issue of:** #4 ✅

Stands up LiteLLM proxy (systemd, Ollama Cloud API key, alias
`voice-agent`→`nemotron-3-super:cloud`); agent uses `openai.LLM` plugin → LiteLLM.
Local Russian `SOUL.md` system prompt. Sentence-chunked TTS streaming. First real
spoken conversation. Realizes ADR-0004.

---

## Slice 4 — Barge-in interruption (close-socket TTS abort)

- **Type:** AFK (`ready-for-agent`)
- **Blocked by:** #13 (Slice 3)
- **Issue:** https://github.com/prineycom/voice-agent/issues/14
- **Sub-issue of:** #4 ✅

User speaking over the agent aborts TTS by closing the socket; agent stops and
re-listens. Fresh TTS socket per turn. Realizes ADR-0006 (interruption).

---

## Slice 5 — Hermes MCP: tools, memory, skills

- **Type:** HITL (`ready-for-human`)
- **Blocked by:** #13 (Slice 3), Hermes MCP service on Pi
- **Issue:** https://github.com/prineycom/voice-agent/issues/15
- **Sub-issue of:** #4 ✅

Wires Hermes MCP over HTTP (standalone service). Agent calls tools, reads memory,
uses skills. Validates nemotron tool-calling reliability + latency over voice.
The deferred "tools" slice. HITL: needs Hermes running + human judgment on
tool-call quality/latency.

---

## Publish notes

- All 5 issues created and linked as sub-issues of #4 (`sub_issues_summary`: 5 total).
- Labels: slices 1–4 `ready-for-agent`; slice 5 `ready-for-human` (labels already existed in repo).
- Issue **types** (`Task`) not set — the repo/account has no GitHub issue types
  configured (the PATCH is accepted but `type` stays null, same as Epic 1). The
  `## Parent` section in each body is the human-readable fallback.
