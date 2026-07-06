# A2F service deploy (production `helper`, containerized)

The real `helper` backend runs the `a2f_stream` binary, which needs
glibc 2.38 / CUDA 13 / TensorRT 10 — satisfied only inside the NVIDIA TensorRT
container, not the WSL2 host. So A2F runs as a **Docker container in WSL2**, not
an NSSM service (WSL2 refuses NSSM's LocalSystem account). See
[ADR 0015](../../../../docs/adr/0015-a2f-helper-production.md).

## Build the image (in WSL2 Ubuntu)

```bash
bash /mnt/e/voice-agent-repo/infra/desktop/a2f/deploy/build_image.sh
```

Bakes the prebuilt spike artifacts (`a2f_stream`, `libaudio2x.so`, the James model
dir minus `network.onnx`) + the FastAPI service into `voice-agent-a2f:latest`.
Artifact sources default to `~/a2f-sdk/…` (override via `SDK`/`JAMES`/`LIBA`/`BIN`).

## Install / update the service (elevated PowerShell on the Desktop)

```powershell
powershell -ExecutionPolicy Bypass -File E:\voice-agent-repo\infra\desktop\a2f\deploy\setup_a2f_service.ps1
```

This (idempotently): removes any stale NSSM attempt, runs the container
`--restart always` with CDI GPU publishing `:8003`, `systemctl enable docker` in
WSL, and registers the **logon Scheduled Task** `voice-agent-a2f-boot`
(`a2f-boot.cmd`) that brings WSL + the container up in the user session.

Health: `curl http://127.0.0.1:8003/health` (Windows→WSL loopback) → `"backend":"helper"`.
Restart: `wsl -d Ubuntu -- docker restart voice-agent-a2f`.

## Enable the TTS → A2F fork

In `infra/desktop/tts/.env`: `A2F_FORK_ENABLED=1`,
`A2F_WS_URL=ws://127.0.0.1:8003/a2f`; then `nssm restart voice-agent-tts`.

## Headless reboot

The container is `--restart always` and the logon task starts it, but WSL2 only
runs in a user session — **headless reboot survival requires Windows autologon**
for the desktop user (see [[desktop-wol-autologon]]). Without autologon, log in
once after a reboot (or the task fires on the next logon).

## Mock fallback

Running `server.py` bare (no container) with `A2F_BACKEND=mock` yields synthetic
GPU-free blendshapes — the CI / frontend-dev default.
