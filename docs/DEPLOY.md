# Deployment & Operations

The stack runs **always-on** across two hosts via native process supervisors
(no full Docker — the GPU services run natively for CUDA). Everything auto-starts
at boot and auto-restarts on failure.

## What runs where

| Host | Service | Supervisor | Port | Notes |
|------|---------|-----------|------|-------|
| Pi (`priney`) | `voice-agent-worker` | systemd | 8090 | LiveKit Agents worker (`python -m agent start`) |
| Pi | `voice-agent-web` | systemd | 8095 | Test-harness (token server + page), bound to `127.0.0.1` |
| Pi | LiveKit SFU | Docker (`voice-agent-livekit`, `restart=unless-stopped`) | 7880 / 8443 | TLS via tailscale-serve |
| Pi | LiteLLM proxy | Docker | 4000 | LLM gateway |
| Desktop (Windows, RTX 4070) | `voice-agent-stt` | NSSM (LocalSystem) | 8001 | faster-whisper large-v3-turbo |
| Desktop | `voice-agent-tts` | NSSM (LocalSystem) | 8002 | Qwen3-TTS, multi-engine |

Desktop services run as **LocalSystem** so they start at boot **without login**
(verified: CUDA is reachable from session 0 on this box). HF model cache and the
Xet workaround are set in each service's environment (`HF_HOME=E:\AI\models`,
`HF_HUB_DISABLE_XET=1` — the Xet CAS endpoint is unreachable here).

## Fast redeploy

One command from the Pi pulls latest `main` on both hosts and restarts services:

```bash
~/repos/voice-agent/deploy.sh            # Pi + Desktop
~/repos/voice-agent/deploy.sh --pi       # Pi only
~/repos/voice-agent/deploy.sh --desktop  # Desktop only
```

It git-pulls the Pi checkout (`~/repos/voice-agent`), restarts the systemd units,
then SSHes to the Desktop and runs `E:\voice-agent\tools\redeploy.ps1` (git pull
of the Desktop checkout + `nssm restart` of STT/TTS), and prints health.

## Pi services (systemd)

Unit files: `infra/pi/agent/deploy/voice-agent-worker.service`,
`infra/pi/web/deploy/voice-agent-web.service` (installed to `/etc/systemd/system/`).

```bash
sudo systemctl status voice-agent-worker voice-agent-web
sudo systemctl restart voice-agent-worker
journalctl -u voice-agent-worker -f
```

## Desktop services (NSSM)

`nssm.exe` lives at `E:\voice-agent\tools\nssm.exe`. Code runs from the git
checkout `E:\voice-agent-repo\infra\desktop\{stt,tts}\server.py` using the
existing venvs at `E:\voice-agent\desktop\{stt,tts}\.venv`.

```powershell
Get-Service voice-agent-stt, voice-agent-tts
E:\voice-agent\tools\nssm.exe restart voice-agent-tts
# logs: E:\voice-agent-repo\infra\desktop\{stt,tts}\service.log
Invoke-RestMethod http://localhost:8002/health
```

### Desktop git checkout

`E:\voice-agent-repo` is a clone of this repo authenticated by a **read-only SSH
deploy key** (`E:\voice-agent\tools\deploy_key`, configured via the repo's
`core.sshCommand`). Runtime data — `.env`, `voices/` — lives in the checkout's
`infra/desktop/tts/` (gitignored). `git pull` + service restart = redeploy.

## Switching the TTS voice

The voice library is `infra/desktop/tts/voices/library.json` in the Desktop
checkout. From `E:\voice-agent-repo\infra\desktop\tts`:

```powershell
powershell -ExecutionPolicy Bypass -File list_voices.ps1
powershell -ExecutionPolicy Bypass -File switch_voice.ps1 -Name jane   # rewrites .env + nssm restart
```

Voices: `jane` (ZZZ clone), `pasha` (own-voice clone), `anime`/`narrator_m`
(voice_design), `aiden` (preset). No worker/web change — TTS is per-session.

## First-time install (reference)

- Pi: `sudo cp <unit> /etc/systemd/system/ && sudo systemctl enable --now <unit>`.
- Desktop: download nssm, generate deploy key + add to repo (read-only), clone to
  `E:\voice-agent-repo`, then `nssm install` the two services as LocalSystem with
  `AppEnvironmentExtra HF_HOME=… HF_HUB_DISABLE_XET=1`, `Start SERVICE_AUTO_START`,
  `AppExit Default Restart`. (Bootstrap scripts under `E:\voice-agent\tools\`.)
