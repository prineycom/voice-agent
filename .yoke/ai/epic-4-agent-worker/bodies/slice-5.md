## Parent

[Epic 4] Agent Worker on Pi 5 — https://github.com/prineycom/voice-agent/issues/4

## What to build

Give the agent **tools, memory, and skills** via **Hermes MCP**. Connect the Agent
Worker to Hermes over **HTTP** (Hermes runs as its own long-lived service on the
Pi, lifecycle decoupled from the agent). With MCP wired into AgentSession, the LLM
can call Hermes tools, read/write persistent memory, and use skills during a voice
conversation. This slice also validates **nemotron's tool-calling reliability over
voice** and the added latency of tool round-trips.

This is the deferred "slice 2" from the grilling (tool calling was intentionally
kept out of the bare voice loop to de-risk the core pipeline first).

Decisions this realizes (from grilling):
- **MCP over HTTP, Hermes standalone** (not stdio subprocess) — memory/skills
  persist across agent restarts.
- Tool calling routed **through Hermes MCP** — SOUL.md, memory, skills (per the
  glossary's [[Hermes MCP]] definition).

Why HITL: requires Hermes running on the Pi and human judgment on tool-call
quality, reliability, and acceptable added latency over voice — not purely
mechanical.

## Acceptance criteria

- [ ] Agent Worker connects to Hermes MCP over HTTP and discovers its tools
- [ ] During a voice conversation the agent can invoke a Hermes tool and use the result in its spoken reply
- [ ] Agent can read and write persistent memory through Hermes (a fact stated in one turn is recalled later)
- [ ] Tool-call reliability with nemotron is assessed; failure/timeout of a tool call degrades gracefully (agent still responds)
- [ ] Added latency from tool round-trips is measured and documented
- [ ] Hermes lifecycle is independent — restarting the agent does not lose memory/skills

## Blocked by

- #13 (Slice 3 — LiteLLM proxy + real conversation)
- Hermes MCP service running on Pi 5
