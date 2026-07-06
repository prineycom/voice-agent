# Report: a2f-helper-production

**Plan:** .yoke/ai/a2f-helper-production/a2f-helper-production-plan.md
**Mode:** sub-agents (orchestrator-driven; live WSL2/Docker over SSH)
**Status:** ✅ complete (browser visual on the domain is the one human-gated check)

## Tasks

| # | Task | Status | Commit | Concerns |
| --- | ---- | ------ | ------ | -------- |
| 1 | Dockerfile + build_image.sh | ✅ DONE | `0b… (branch)` | image `voice-agent-a2f:latest` builds |
| 2 | Build image + GPU smoke | ✅ DONE | — | 18 frames×68, VRAM Δ403 MiB |
| 3 | Windows→WSL port bridge | ✅ DONE | — | `curl.exe 127.0.0.1:8003` ok |
| 4 | Always-on service | ⚠️ DONE_WITH_CONCERNS | `5101224` | NSSM→restart=always+logon task (WSL≠LocalSystem) |
| 5 | Enable TTS→A2F fork | ✅ DONE | — | `.env` set, TTS restarted |
| 6 | Repo integration + docs | ✅ DONE | `c0ae22d` | ADR 0015 + unstale READMEs |
| 7 | E2E acceptance | ✅ DONE | — | 195 blendshape frames e2e |

## What is live now

- **`voice-agent-a2f` container** on the Desktop RTX 4070: `voice-agent-a2f:latest`
  (FROM `nvcr.io/nvidia/tensorrt:25.08-py3`), `--restart always`, CDI GPU, serving
  `helper` backend on `:8003`. Built one-command via `deploy/build_image.sh`.
- **Supervision:** dockerd `systemctl enable`d in WSL + logon Scheduled Task
  `voice-agent-a2f-boot` (`deploy/a2f-boot.cmd`) brings WSL + the container up in the
  user session.
- **TTS fork enabled** (`A2F_FORK_ENABLED=1`, `A2F_WS_URL=ws://127.0.0.1:8003/a2f`),
  TTS restarted. Agent worker already forwards blendshapes; frontend renders A2F by default.
- Repo merged to `main` (`f9c768d`); Pi + Desktop checkouts on latest.

## Measured acceptance

| Criterion | Result |
| --- | --- |
| End-to-end real A2F blendshapes | ✅ Pi→Desktop TTS over Tailscale, one utterance → **195 `blendshapes` frames** (real ARKit coeffs) returned via the `/tts` multiplex (TTS synth → fork → A2F helper GPU → back) |
| VRAM no OOM | ✅ engine Δ **403 MiB**; STT+TTS+A2F resident, ~4.0 GB free of 12 GB |
| Reproducibility | ✅ `build_image.sh` builds `voice-agent-a2f:latest` from staged prebuilt artifacts in one command |
| Browser face on domain | ⏳ human-gated (mic + WebRTC) — server-side path proven; frontend A2F rendering is default-on |

## Concerns

### Task 4: supervision mechanism changed (NSSM → restart=always + logon task)

The grill picked an NSSM wrapper, but **WSL2 refuses to run under NSSM's LocalSystem
account** (`WSL_E_LOCAL_SYSTEM_NOT_SUPPORTED`). Adapted to the standard WSL-service
pattern: container `--restart always` + `systemctl enable docker` + a logon Scheduled
Task. **Headless reboot survival additionally requires Windows autologon** for the
desktop user — currently `AutoAdminLogon` is not set in the registry, so after a cold
reboot the container comes up on the next interactive logon (documented in ADR 0015 /
deploy/README; not a gated acceptance criterion).

## Validation

- Direct A2F smoke (in the production container): 18 frames × 68 coeffs ✅
- Windows→WSL loopback `curl 127.0.0.1:8003/health` → `backend:helper` ✅
- Pi→Desktop TTS-fork-A2F over Tailscale: 195 blendshape frames ✅
- `voice-agent-a2f` container Up, health green after service (re)start ✅

## Changes summary

| File | Action | Description |
| --- | --- | --- |
| infra/desktop/a2f/deploy/Dockerfile | created | image baking prebuilt artifacts + service |
| infra/desktop/a2f/deploy/build_image.sh | created | reproducible image build (WSL) |
| infra/desktop/a2f/deploy/a2f-boot.cmd | created | logon-task boot script |
| infra/desktop/a2f/deploy/setup_a2f_service.ps1 | created | idempotent prod setup (container+task) |
| infra/desktop/a2f/deploy/README.md | rewritten | container deploy recipe (was stale NSSM/mock) |
| infra/desktop/a2f/README.md | modified | unstale status → production |
| docs/adr/0015-a2f-helper-production.md | created | decision record |
| docs/DEPLOY.md, CLAUDE.md | modified | voice-agent-a2f row + non-obvious note |

## Commits (branch → main `f9c768d`)

- `docs: add implementation plan`
- `feat: Dockerfile + build script`
- `feat: NSSM wrapper + installer` → superseded by
- `fix: restart=always container + logon task, not NSSM`
- `docs: ADR 0015 + DEPLOY/CLAUDE + unstale READMEs`
- `Merge a2f-helper-production (f9c768d)`

## Human step to finish acceptance

Open `ai.priney.com`, allow the mic, and talk to the agent — the Live2D face should
animate from A2F blendshapes (use `?lipsync=volume` only to force the fallback for
comparison). For headless reboot resilience, enable Windows autologon on the desktop.
