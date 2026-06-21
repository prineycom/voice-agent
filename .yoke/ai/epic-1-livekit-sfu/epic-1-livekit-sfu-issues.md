# Epic 1 — LiveKit SFU on Pi 5: Issue Breakdown

Parent epic: [Epic 1] LiveKit SFU on Pi 5 — https://github.com/prineycom/voice-agent/issues/1

Vertical tracer-bullet slices. Dependency order: 1 → 2.

Related docs: `CONTEXT.md`, `docs/adr/0005-edge-tls-caddy.md`.

---

## Slice 1 — LiveKit SFU up via Docker Compose + headless audio smoke test

- **Type:** AFK (`ready-for-agent`)
- **Blocked by:** None
- **Issue:** https://github.com/prineycom/voice-agent/issues/8
- **Sub-issue of:** #1 ✅

Stand up the SFU via Docker Compose on Pi 5 and prove two `lk` CLI participants exchange
audio over `ws://` on LAN/Tailscale — no TLS, no browser. `infra/pi/` with
`docker-compose.yml` (host networking, pinned image, `restart: unless-stopped`, no Redis),
`livekit.yaml` (ports 7880/7881/50000-60000, `use_external_ip: false` → private host
candidates, env-substituted keys), `.env.example`. README: compose up/down/logs + key gen.

---

## Slice 2 — Caddy TLS edge + real browser two-participant audio

- **Type:** HITL (`ready-for-human`)
- **Blocked by:** #8 (Slice 1)
- **Issue:** https://github.com/prineycom/voice-agent/issues/9
- **Sub-issue of:** #1 ✅

Add Caddy as the TLS-terminating edge proxy in the compose stack, holding a Tailscale cert
for `priney-pi.<tailnet>.ts.net`, proxying WSS → LiveKit. Verify via `meet.livekit.io`
(HTTPS, mic allowed) + `lk` token: two browser participants exchange audio over
`wss://priney-pi.<tailnet>.ts.net`. Realizes ADR-0005. HITL: human runs `tailscale cert`
and grants mic at the browser.

---

## Publish notes

- Labels `ready-for-agent` and `ready-for-human` created in repo.
- Both issues linked as sub-issues of #1.
- Issue **types** (`Task`) not set — repo/account has no GitHub issue types configured.
  The `## Parent` section in each body is the human-readable fallback.
