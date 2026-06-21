---
tags:
  - voice-agent
  - adr
status: accepted
---

# LLM Model: nemotron-3-super via Ollama Cloud

Start with nemotron-3-super:cloud (Ollama Cloud) — fastest available model in the subscription (~1.6s TTFT), tool calling supported. Decision is non-binding: LiteLLM config swap is one line, so model experimentation is cheap. Industry research shows GPT-4.1 (300ms TTFT) and Gemini 2.5 Flash (450ms TTFT) are the production leaders for voice agents, but neither is available in Ollama Cloud. Can add direct API keys to LiteLLM later.

## Considered Options

- **nemotron-3-super:cloud** ✅ — ~1.6s TTFT, tool calling, available in subscription
- **gemini-3-flash-preview:cloud** ❌ — ~2.1s TTFT, unstable (8s+ spikes), preview model
- **qwen3-coder-next:cloud** ❌ — ~2.1s TTFT, coding-oriented
- **glm-5.2:cloud** ❌ — ~20-24s TTFT, completely unsuitable for voice
- **GPT-4.1 via direct API** ⏳ — 300ms TTFT, $2/$8 per 1M, industry default. Add API key to LiteLLM when ready.

## Consequences

- Total pipeline latency estimate: STT (~300ms) + LLM TTFT (~1.6s) + LLM stream (~300ms) + TTS (~300ms) = ~2.5s
- Not realtime, but acceptable for MVP. "Walkie-talkie" experience.
- Upgrade path: add Gemini 2.5 Flash or GPT-4.1 API key to LiteLLM config → instant improvement