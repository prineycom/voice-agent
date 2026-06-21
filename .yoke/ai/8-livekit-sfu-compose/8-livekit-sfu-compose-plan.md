---
ticket: "#8"
slug: 8-livekit-sfu-compose
update_docs: false
---

# Plan: LiveKit SFU up via Docker Compose + headless audio smoke test

**Mode:** sub-agents (executed inline by orchestrator — infra task needs live docker/network output)
**Parallel:** false (all tasks touch `infra/pi/`, strict sequence)
**Ticket:** #8

## Design decisions

**DD-1 — LiveKit container uses host networking.**
Decision: `network_mode: host` for the LiveKit service.
Rationale: the RTC media port range (50000-60000/udp) cannot be cleanly published through
Docker's NAT; host networking is the documented LiveKit pattern on Linux. The Pi is a
single-purpose edge node, so host networking is acceptable.
Alternative: explicit port mapping — rejected, breaks ICE for the UDP range.

**DD-2 — Private host ICE candidates, no TURN.**
Decision: `rtc.use_external_ip: false`.
Rationale: per ADR-0005, the network is private (LAN + Tailscale); LiveKit gathers host
candidates on all interfaces and the client picks a reachable one.
Alternative: `use_external_ip: true` / TURN — rejected, only needed for public/NAT traversal.

**DD-3 — Keys via env substitution, single-node, no Redis.**
Decision: `keys` map in `livekit.yaml` reads `${LIVEKIT_API_KEY}`/`${LIVEKIT_API_SECRET}`;
no Redis block.
Rationale: single-node SFU needs no Redis; `.env` (gitignored) is the single secret source
reused by later epics. `.env.example` committed.
Alternative: keys inline in yaml — rejected, would commit secrets or duplicate them.

**DD-4 — Pinned image, `restart: unless-stopped`.**
Decision: pin `livekit/livekit-server` to a specific version tag; `restart: unless-stopped`.
Rationale: reproducibility across the 7-epic build; restart policy gives reboot survival.
Alternative: `latest` — rejected, non-reproducible.

## Tasks

### Task 1 — Author `infra/pi/` config
**Files:** `infra/pi/docker-compose.yml`, `infra/pi/livekit.yaml`, `infra/pi/.env.example`
**Depends on:** none
**Scope:** config authoring only.
**What:** create the compose stack (LiveKit only for #8; Caddy is #9), the LiveKit config, and
the committed env template.
**How:** DD-1..DD-4. Ports 7880 (signaling), 7881 (RTC/TCP), 50000-60000/udp. `log_level: info`.
**Verify:** `docker compose -f infra/pi/docker-compose.yml config` parses clean.

### Task 2 — Generate keys + bring the SFU up
**Files:** `infra/pi/.env` (gitignored, not committed)
**Depends on:** Task 1
**Scope:** runtime bring-up.
**What:** generate an API key/secret pair into `.env`, `docker compose up -d`, confirm healthy.
**How:** key = short id, secret = random (openssl). Pull pinned image, start, check logs for
"starting LiveKit server" and the listen ports.
**Verify:** `docker compose ps` shows running; `curl -s localhost:7880` responds (LiveKit
returns "OK" on the HTTP probe); logs show no fatal errors.

### Task 3 — Install `lk` + two-participant audio smoke test
**Files:** none (verification)
**Depends on:** Task 2
**Scope:** end-to-end headless verification.
**What:** install the `lk` CLI, mint a token, run two participants in one room — one publishes
a demo audio track, the other subscribes — confirm subscription over `ws://` on LAN and the
Tailscale IP.
**How:** `lk` from the official install script; `lk token create`; `lk room join --publish-demo`
for the publisher and a plain join for the subscriber; assert the subscriber logs the remote
audio track. Repeat the join against `ws://100.108.52.92:7880` (Tailscale) to confirm both paths.
**Verify:** subscriber reports a subscribed remote audio track from the publisher on both
`ws://localhost` and `ws://<tailscale-ip>`.

### Task 4 — README runbook
**Files:** `README.md`
**Depends on:** Task 1
**Scope:** docs.
**What:** document `docker compose up -d` / `down` / `logs`, the key-generation step, and the
`lk` smoke-test commands. Extend the existing README, do not restructure it.
**Verify:** commands in the README match what was actually run.

### Task 5 — Validation
**Depends on:** all
**What:** re-run `docker compose config`, confirm `.env` is gitignored and not staged, confirm
the SFU is up.

## Execution

**Mode:** sub-agents · **Parallel:** false
**Reasoning:** five tasks all centered on `infra/pi/`, strictly sequential (config → up → verify),
each requiring live command output the orchestrator observes directly.
**Order:** Task 1 → Task 2 → Task 3 → (Task 4 can interleave after Task 1) → Task 5.

## Verification (acceptance criteria from #8)

- [ ] compose runs LiveKit with host networking, pinned image, `restart: unless-stopped`
- [ ] `livekit.yaml`: ports 7880/7881/50000-60000, `use_external_ip: false`, no Redis, env-substituted keys
- [ ] key/secret generated; `.env.example` committed, `.env` gitignored
- [ ] `docker compose up -d` brings the SFU up; survives reboot (restart policy) — *reboot test deferred to human; policy verified configured*
- [ ] two `lk` participants exchange audio publish/subscribe over `ws://` on LAN and Tailscale
- [ ] README documents up/down/logs + key generation

## Notes / risks surfaced for the confirmation gate

1. **`lk` CLI not installed** — Task 3 installs it. If the install is declined, the audio smoke
   test cannot run and that AC is reported as blocked.
2. **Reboot survival** — I will NOT reboot the Pi (it would kill this session). I set
   `restart: unless-stopped` and verify the policy; the actual reboot check is left to the human.
3. **Tailnet domain mismatch** — real Pi node is `rpi.tail29685.ts.net` (IP 100.108.52.92), not
   `priney-pi.<tailnet>` as CLAUDE.md/ADR-0005 assume. Out of scope for #8 (no TLS) but must be
   reconciled in #9 — will flag in the report.
