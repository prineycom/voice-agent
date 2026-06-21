## Parent

[Epic 1] LiveKit SFU on Pi 5 — #1

## What to build

Put a Caddy Edge Proxy in front of the running SFU so a real browser can join over a trusted secure context, and prove two-participant audio through the browser path. This realizes ADR-0005 (`docs/adr/0005-edge-tls-caddy.md`).

The slice adds Caddy to the Docker Compose stack as the single TLS-terminating entry point. Caddy holds a Tailscale-provisioned Let's Encrypt certificate for the Tailnet Domain (`priney-pi.<tailnet>.ts.net`) and reverse-proxies WSS → LiveKit signaling. Because the browser microphone (`getUserMedia`) and a WSS connection both require a Secure Context, the trusted `*.ts.net` certificate is what makes the browser path work — plain LAN hostname/IP would not.

Verification uses the hosted `meet.livekit.io` client (served over HTTPS, so the mic is allowed) pointed at `wss://priney-pi.<tailnet>.ts.net` with a token minted by `lk`. Two tabs/devices on the tailnet join the same room and exchange live audio. Pure-LAN clients without Tailscale are explicitly out of scope (per ADR-0005).

This is HITL: it requires a human to run the `tailscale cert` step and to sit at a browser granting microphone permission and confirming audio.

## Acceptance criteria

- [ ] A Tailscale certificate for `priney-pi.<tailnet>.ts.net` is provisioned and available to Caddy
- [ ] Caddy is added to `infra/pi/docker-compose.yml` as the TLS-terminating edge, proxying WSS → LiveKit (`localhost:7880`)
- [ ] A browser on the tailnet loads `meet.livekit.io`, connects to `wss://priney-pi.<tailnet>.ts.net` with an `lk`-minted token, and the mic is permitted (secure context works)
- [ ] Two browser participants exchange live audio through the SFU over the Tailnet Domain
- [ ] README documents the certificate-provisioning steps and how to connect a browser (token + URL)

## Blocked by

- LiveKit SFU up via Docker Compose + headless audio smoke test — #8
