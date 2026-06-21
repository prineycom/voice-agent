## Parent

[Epic 1] LiveKit SFU on Pi 5 — #1

## What to build

Stand up the LiveKit SFU on Pi 5 via Docker Compose and prove a working end-to-end media path between two participants headlessly — before any TLS or browser is involved.

The slice delivers a running SFU configured for a private LAN + Tailscale network: LiveKit listens on its standard ports, advertises private host ICE candidates (no TURN), and authenticates with an API key/secret pair sourced from a gitignored `.env`. Verification is two `lk` CLI participants joining the same room and exchanging audio over `ws://` on the LAN/Tailscale — no certificate, no proxy, no browser.

Configuration lives under `infra/pi/` (per the agreed monorepo layout — this directory becomes the template for later node-scoped services). Single-node, so no Redis.

Key config decisions (from this session, see `docs/adr/0005-edge-tls-caddy.md` and `CONTEXT.md`):
- LiveKit container uses host networking (required so the UDP media port range works without NAT mangling).
- `use_external_ip: false` → LiveKit gathers private host candidates on all interfaces (LAN + Tailscale); the client picks a reachable one.
- API key/secret injected via environment substitution; `.env` is gitignored, `.env.example` is committed.
- Image version is pinned (not `latest`); `restart: unless-stopped`.

## Acceptance criteria

- [ ] `infra/pi/docker-compose.yml` runs LiveKit with host networking, a pinned image version, and `restart: unless-stopped`
- [ ] `infra/pi/livekit.yaml` configures signaling/RTC ports (7880, 7881, 50000-60000/udp), `use_external_ip: false`, no Redis, and reads keys via env substitution
- [ ] An API key/secret pair is generated; `.env.example` is committed and `.env` stays gitignored
- [ ] `docker compose up -d` brings the SFU up; it survives a Pi reboot (comes back via restart policy)
- [ ] Two `lk` CLI participants join the same room and audio publish/subscribe is confirmed over `ws://` on LAN and over Tailscale
- [ ] README documents `docker compose up -d` / `down` / `logs` and the key-generation step

## Blocked by

None - can start immediately
