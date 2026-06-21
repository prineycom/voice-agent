# Report: 8-livekit-sfu-compose

**Plan:** `.yoke/ai/8-livekit-sfu-compose/8-livekit-sfu-compose-plan.md`
**Mode:** sub-agents (executed inline — infra task on the live Pi)
**Status:** ✅ complete

## Tasks

| #   | Task                                   | Status  | Commit    | Concerns |
| --- | -------------------------------------- | ------- | --------- | -------- |
| 1   | Author `infra/pi/` config              | ✅ DONE | `f0482b8` | —        |
| 2   | Generate keys + bring the SFU up       | ✅ DONE | `f0482b8` | —        |
| 3   | Install `lk` + two-participant audio   | ✅ DONE | `f0482b8` | —        |
| 4   | README runbook                         | ✅ DONE | `f0482b8` | —        |
| 5   | Validation                             | ✅ DONE | —         | —        |

## Post-implementation

| Step          | Status     | Commit |
| ------------- | ---------- | ------ |
| Validate      | ✅ pass    | —      |
| Documentation | ⏭️ skipped (README handled in Task 4) | — |
| Format        | ⏭️ N/A (yaml/md only) | — |

## Validation

- `docker compose config` ✅ parses clean
- `docker inspect` restart policy ✅ `unless-stopped`
- Container state ✅ `running`; `curl localhost:7880/` → `200 OK`
- `.env` ✅ gitignored, not staged (only `.env.example` committed)
- Audio smoke test ✅ over `ws://localhost:7880` AND `ws://100.108.52.92:7880` (Tailscale):
  subscriber logged `track subscribed {kind: audio}` from the publisher on both paths

## Acceptance criteria (#8)

- [x] compose runs LiveKit with host networking, pinned image (v1.13.1), `restart: unless-stopped`
- [x] `livekit.yaml`: ports 7880/7881/50000-60000, `use_external_ip: false`, no Redis, keys via env
- [x] key/secret generated; `.env.example` committed, `.env` gitignored
- [~] `docker compose up -d` brings the SFU up — **reboot survival not tested** (would kill this
      session); `restart: unless-stopped` verified configured and in effect
- [x] two `lk` participants exchange audio publish/subscribe over `ws://` on LAN + Tailscale
- [x] README documents up/down/logs + key generation + smoke test

## Changes summary

| File                         | Action   | Description                                  |
| ---------------------------- | -------- | -------------------------------------------- |
| `infra/pi/docker-compose.yml`| created  | LiveKit v1.13.1, host net, restart policy    |
| `infra/pi/livekit.yaml`      | created  | ports, private host ICE, no Redis            |
| `infra/pi/.env.example`      | created  | committed credential template                |
| `infra/pi/.env`              | created  | generated key/secret (gitignored)            |
| `README.md`                  | modified | SFU runbook + audio smoke test               |

## Commits

- `f0482b8` #8 feat(8-livekit-sfu-compose): add LiveKit SFU compose stack + smoke-test runbook
- `8fadb32` #1 docs: add ADR-0005, glossary terms, Epic 1 issue index

## Notes / follow-ups

1. **Reboot survival** — left for you: `sudo reboot`, then `docker ps` should show
   `voice-agent-livekit` back up via the restart policy.
2. **Tailnet domain mismatch (affects #9 / ADR-0005)** — the real Pi node is
   `rpi.tail29685.ts.net` (`100.108.52.92`), not `priney-pi.<tailnet>`. ADR-0005 and CLAUDE.md
   should be reconciled to the actual name before the Caddy TLS slice (#9).
3. `lk` CLI (2.16.6) and a generated `/tmp/tone.ogg` were used for the smoke test; the CLI is
   now installed at `/usr/local/bin/lk`.
