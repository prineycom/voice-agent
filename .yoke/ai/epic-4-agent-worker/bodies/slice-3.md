## Parent

[Epic 4] Agent Worker on Pi 5 — https://github.com/prineycom/voice-agent/issues/4

## What to build

The first **real spoken conversation** — swap the echo for an actual LLM brain.
Stand up **LiteLLM** as a standalone proxy on the Pi (its own systemd service)
exposing an OpenAI-compatible endpoint, with a model alias
`voice-agent` → `nemotron-3-super:cloud` routed to Ollama Cloud. The Agent Worker
reaches it through LiveKit's stock `openai.LLM` plugin (`base_url` = the local
LiteLLM endpoint). A local **`SOUL.md`** in the Agent Worker repo supplies the
Russian personality/system prompt, read at session start.

The LLM streams tokens; the **TTS plugin chunks the stream into sentences** (one
`/tts` request per sentence) so the agent starts speaking after sentence #1.
Result: the user speaks Russian, the agent reasons and replies in character,
spoken aloud.

Decisions this realizes (from grilling + ADR-0004):
- **LiteLLM proxy + openai plugin** (not the litellm SDK in-process) — model swaps
  stay a one-line config change.
- **Ollama Cloud auth via API key** in the LiteLLM config (env var), not
  `ollama login` — non-interactive, survives restarts.
- **Sentence chunking** via LiveKit's StreamAdapter/sentence tokenizer. ⚠️ Verify
  the tokenizer splits **Russian** punctuation correctly; add a custom splitter
  if it mis-splits Cyrillic/abbreviations.
- **Personality from a local `SOUL.md`** file (no Hermes dependency here).
- Latency target ≤1500ms is the post-upgrade north-star; nemotron MVP runs
  ~2.2–2.5s ("walkie-talkie") and that is acceptable for this slice.

## Acceptance criteria

- [ ] LiteLLM runs as a Pi systemd service, OpenAI-compatible endpoint up, alias `voice-agent`→`nemotron-3-super:cloud`
- [ ] Ollama Cloud authenticated via API key referenced from LiteLLM config (no interactive login)
- [ ] Agent uses the `openai.LLM` plugin pointed at LiteLLM; model swap is a one-line config change
- [ ] `SOUL.md` (Russian) is loaded as the system prompt and the agent's replies reflect its personality
- [ ] LLM output is sentence-chunked into TTS so the agent begins speaking before the full response is generated
- [ ] A multi-turn Russian voice conversation works end-to-end; first-audio latency is measured and logged
- [ ] Russian sentence tokenization verified (no audibly wrong sentence splits)

## Blocked by

- #12 (Slice 2 — STT echo loop)
