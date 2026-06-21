---
tags:
  - voice-agent
  - adr
status: accepted
---

# Private Access via Tailscale Cert, TLS Terminated at Caddy Edge Proxy

The platform is reachable only on a private network — LAN and Tailscale, no public
internet exposure. The browser microphone (`getUserMedia`) and LiveKit signaling require
a Secure Context (trusted HTTPS/WSS), so Pi 5 serves a real Let's Encrypt certificate
provisioned by Tailscale for its Tailnet Domain (`priney-pi.<tailnet>.ts.net`). TLS is
terminated at a single Caddy Edge Proxy that fronts LiveKit signaling (WSS) and, later,
the static frontend.

## Considered Options

### TLS provisioning
- **Tailscale cert (`*.ts.net`)** ✅ — real, auto-renewing Let's Encrypt cert; trusted on
  every Tailscale device with no custom CA to install (kiosk included). Does not cover
  pure-LAN clients without Tailscale.
- **mkcert / local CA** ❌ — works over LAN, but the CA must be installed on every device,
  including the kiosk; brittle and manual.
- **localhost only** ❌ — defers the foundational connectivity choice to Epic 5/6 and leaves
  Epic 1 unable to prove the real browser path.

### TLS termination point
- **Caddy reverse proxy** ✅ — one entry point and one origin for LiveKit WSS and the future
  frontend; clean path routing for Epics 5/6; takes the Tailscale cert directly.
- **`tailscale serve`** ❌ — zero extra components, but tightly couples routing to Tailscale
  and offers less control over headers/paths.
- **LiveKit native TLS** ❌ — fewer moving parts, but the frontend would need separate serving
  and the client would juggle multiple ports/origins.

### Network exposure
- **Private (LAN + Tailscale)** ✅ — sufficient for all project nodes (Pi, Desktop, kiosk,
  developer browser); no TURN, no port-forwarding, no public attack surface.
- **Public internet** ❌ — needs a public domain, hardened port exposure, and TURN; large
  scope beyond the project's needs today.

## Consequences

- Pi 5 runs Caddy as the Edge Proxy in the same Docker Compose stack as LiveKit.
- Caddy holds the Tailscale-provisioned cert for the Tailnet Domain and proxies WSS → LiveKit.
- Clients connect via `wss://priney-pi.<tailnet>.ts.net`; pure-LAN clients without Tailscale
  are out of scope.
- WebRTC media uses private host ICE candidates over LAN/Tailscale interfaces; no TURN server.
- The same single-origin model is inherited by the frontend (Epic 5) and kiosk (Epic 6).
- Moving to public access later means adding a public domain + TURN — a deliberate, separate
  decision, not an accident of this setup.
