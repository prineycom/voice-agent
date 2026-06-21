# Code Review: 8-livekit-sfu-compose

## Summary

### Context and goal
Issue #8 (Slice 1 of Epic 1) stands up a LiveKit SFU on Pi 5 via Docker Compose and proves
end-to-end audio between two headless `lk` CLI participants over `ws://` on LAN and Tailscale.
No TLS, no browser — the foundation before Caddy (#9). Scope: `infra/pi/` (compose + LiveKit
config + env template) plus a README runbook.

### Key code areas for review
1. **`infra/pi/docker-compose.yml`** — single-service compose; host networking, pinned image, `LIVEKIT_KEYS` env construction.
2. **`infra/pi/livekit.yaml`** — ports, ICE settings, single-node (no Redis).
3. **`infra/pi/.env.example`** — committed credential template.
4. **`README.md`** — key generation, lifecycle commands, `lk` smoke-test runbook.

### Complex decisions
1. **`network_mode: host`** (`docker-compose.yml:13`) — required for the RTC UDP range; trades container isolation for ICE correctness on a single-purpose node.
2. **`LIVEKIT_KEYS` from two `.env` vars** (`docker-compose.yml`) — keeps a canonical key/secret pair for downstream epics; format-sensitive YAML quoting.
3. **`use_external_ip: false`** (`livekit.yaml`) — private host candidates only; relies on all advertised interfaces being reachable.

### Questions for the reviewer
1. Is LAN access to port 7880 intentionally open to all LAN hosts, or should a `ufw` rule scope it (deferred hardening)?
2. Should the SFU expose a `healthcheck` so later epics can gate on readiness (`docker compose up --wait`)?

### Risks and impact
Low operational risk — config is straightforward and the smoke test passed on both paths.
Secrets handling is correct (`.env` gitignored, not committed). Residual: OS-level visibility of
`LIVEKIT_KEYS` via `docker inspect` (documented), and reboot survival not yet exercised.

### Tests and manual checks
Validated: `docker compose config` parses; `curl localhost:7880/` → `200 OK`; restart policy
`unless-stopped`; `.env` not staged; two-participant `track subscribed {kind: audio}` over
`ws://localhost:7880` and `ws://100.108.52.92:7880`. Reboot survival deferred to human.

### Out of scope
Caddy TLS termination (#9), browser smoke test, TURN, public exposure, firewall hardening,
Tailnet domain correction, container healthcheck.

## Commits

| Hash      | Description                                                       |
| --------- | ----------------------------------------------------------------- |
| `f0482b8` | feat(8-livekit-sfu-compose): add LiveKit SFU compose stack + runbook |
| `4bb12ea` | fix(8-livekit-sfu-compose): fix 5 review issues                   |

## Changed Files

| File                          | +/-   | Description                          |
| ----------------------------- | ----- | ------------------------------------ |
| `infra/pi/docker-compose.yml` | +26   | LiveKit v1.13.1, host net, keys, boundary comment |
| `infra/pi/livekit.yaml`       | +20   | ports, private host ICE, no Redis    |
| `infra/pi/.env.example`       | +11   | committed credential template        |
| `README.md`                   | +60   | SFU runbook + pinned smoke test      |

## Issues Found

| Severity  | Score | Category      | File:line                  | Description |
| --------- | ----- | ------------- | -------------------------- | ----------- |
| Important | 62    | security      | `README.md` smoke test     | `lk` CLI installed unpinned via `\| bash`, not reproducible vs pinned server |
| Minor     | 38    | documentation | `README.md` smoke test     | `ffmpeg` prerequisite not stated |
| Minor     | 30    | documentation | `README.md` smoke test     | second shell missing `.env`/`LIVEKIT_URL` exports → auth fails |
| Minor     | 25    | security      | `docker-compose.yml:21`    | `LIVEKIT_KEYS` readable via `docker inspect` (OS-level, not git) |
| Minor     | 20    | quality       | `docker-compose.yml`       | no `healthcheck` for scripted bring-up |
| Minor     | 18    | documentation | `README.md` smoke test     | tested CLI version not recorded |

## Fixed Issues

| Issue                                   | Commit    | Description |
| --------------------------------------- | --------- | ----------- |
| Unpinned `lk` CLI install (#1)          | `4bb12ea` | Pinned to tagged v2.16.6 release tarball |
| Missing `ffmpeg` prerequisite (#2)      | `4bb12ea` | Added prerequisite line |
| Second shell missing env exports (#3)   | `4bb12ea` | Runbook now sources `.env` + `LIVEKIT_URL` in both shells |
| `docker inspect` key exposure (#4)      | `4bb12ea` | Documented exposure boundary in compose comment |
| CLI version not recorded (#6)           | `4bb12ea` | "tested with lk v2.16.6" noted at install |

## Skipped Issues

| Issue                            | Reason |
| -------------------------------- | ------ |
| No `healthcheck` (#5, Minor 20)  | Suggested `curl` healthcheck likely fails — the `livekit-server` image has no `curl`. Defer to a later automation/hardening epic with a tool that exists in-image (or a TCP probe). |

## Recommendations

- Before #9: reconcile the Tailnet domain (`rpi.tail29685.ts.net`, not `priney-pi.<tailnet>`) in CLAUDE.md and ADR-0005.
- Run the reboot-survival check (`sudo reboot` → `docker ps`) to close the last acceptance criterion.
- Consider a non-`curl` healthcheck (TCP probe / `livekit-server` readiness) when later epics need to gate on SFU readiness.
- Consider scoping port 7880 with `ufw` if LAN exposure should be restricted.
