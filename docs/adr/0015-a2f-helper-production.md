---
tags:
  - voice-agent
  - adr
status: accepted
---

# A2F helper in production: containerized Audio2Face-3D on the Desktop GPU

Ship the real A2F facial-animation `helper` backend (not the `mock`) as an
always-on service so the Live2D face on `ai.priney.com` animates from actual
speech via ARKit blendshapes. All pipeline code (TTS fork → agent forward →
frontend consume) was already merged (issues #33/#34/#35/#36); this ADR is about
**deployment**.

## Context

- The `a2f_stream` C++ helper needs **glibc 2.38 / CUDA 13 / TensorRT 10**. It was
  built in `nvcr.io/nvidia/tensorrt:25.08-py3` (Ubuntu 24.04). The **WSL2 host is
  Ubuntu 22.04 / glibc 2.35**, so the binary cannot run bare on the host — but
  `ldd` inside the base image resolves cleanly. → run the service **in the container**.
- The batch-1 TensorRT engine + James model files + `libaudio2x.so` are prebuilt
  (spike, issue #34), validated at **~403 MiB VRAM**, ~81 ms/utterance, 68 ARKit
  coeffs/frame.

## Decisions

- **Reproducible Docker image** `voice-agent-a2f:latest`, built by
  `infra/desktop/a2f/deploy/build_image.sh` from a Dockerfile
  (`FROM nvcr.io/nvidia/tensorrt:25.08-py3`). It **bakes** the prebuilt artifacts
  (James model dir minus `network.onnx`, `a2f_stream`, `libaudio2x.so`) + the
  FastAPI service + pip deps. `A2F_BACKEND=helper`, serves `:8003`. The SDK is NOT
  rebuilt — artifacts are treated as prebuilt inputs (not in git; 160 MB engine).
- **GPU** via WSL2 CDI (`--device nvidia.com/gpu=all`).
- **Supervision:** NOT NSSM — WSL2 refuses to launch under NSSM's LocalSystem
  account (`WSL_E_LOCAL_SYSTEM_NOT_SUPPORTED`). Instead: the container runs
  `--restart always`, `dockerd` is `systemctl enable`d in WSL, and a **logon
  Scheduled Task** (`a2f-boot.cmd`, `voice-agent-a2f-boot`) brings WSL + the
  container up in the user session. Headless reboot survival additionally requires
  Windows **autologon** for the desktop user (a machine prerequisite).
- **Enable switch:** the Desktop TTS service forks each utterance's PCM+emotion to
  A2F (`A2F_FORK_ENABLED=1`, `A2F_WS_URL=ws://127.0.0.1:8003/a2f`, loopback via WSL2
  localhost forwarding). The agent worker forwards blendshapes to the `voiceagent`
  data channel unconditionally (`TTS_STREAMING` default True). The frontend renders
  A2F by default (volume-lipsync is the fallback / `?lipsync=volume`).

## Consequences

- Real speech-driven facial animation live; VRAM Δ≈0.4 GB co-resides with STT+TTS.
- The A2F service is a WSL2/Docker component (unlike the native STT/TTS NSSM
  services) — a deliberate exception forced by the binary's runtime requirements.
- Rebuild is one command (`build_image.sh`); the ad-hoc spike container is no longer
  the runtime.
- Headless-reboot resilience depends on autologon (documented, not gated).

## Alternatives considered

- **NSSM LocalSystem** (like STT/TTS) ❌ — WSL2 won't run under LocalSystem.
- **Rebuild a2f_stream for the 22.04 host / upgrade WSL to 24.04** ❌ — unnecessary;
  the container already satisfies the runtime and matches how it was built.
- **NIM (audio2face-3d:2.0.0)** ❌ — ~8.8 GB VRAM, won't coexist with STT+TTS (spike).
- **`docker commit` the ad-hoc container** ❌ — opaque, non-reproducible.
