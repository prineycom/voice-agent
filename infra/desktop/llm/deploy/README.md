# LLM service deploy (NSSM)

Mirrors the STT/TTS/A2F NSSM setup (see [docs/DEPLOY.md](../../../../docs/DEPLOY.md),
[[voice-agent-deployment]]). Runs as LocalSystem, boot-start, auto-restart.

Unlike STT/TTS, this service runs `llama-server.exe` directly (no repo venv), so
it does **not** depend on the Desktop repo checkout — only on
`E:\AI\llama.cpp\bin` and the model on `E:\AI\models`.

The service runs the launch script straight from the git checkout
(`E:\voice-agent-repo\infra\desktop\llm\`, like STT/TTS/A2F), so `git pull`
deploys new flags — no file copying.

```powershell
# one-time (elevated)
$nssm = "E:\voice-agent\tools\nssm.exe"
$repo = "E:\voice-agent-repo\infra\desktop\llm"

# Launch via the wrapper script so all flags stay in one place.
& $nssm install voice-agent-llm powershell.exe "-NoProfile -ExecutionPolicy Bypass -File $repo\start_llm.ps1"
& $nssm set voice-agent-llm AppDirectory "$repo"
& $nssm set voice-agent-llm Start SERVICE_AUTO_START
& $nssm set voice-agent-llm AppStdout "$repo\nssm-out.log"
& $nssm set voice-agent-llm AppStderr "$repo\nssm-err.log"
& $nssm start voice-agent-llm

netsh advfirewall firewall add rule name=voiceagent-llm dir=in action=allow protocol=TCP localport=8004
```

Health: `curl http://100.75.88.35:8004/health` (from the Pi, over Tailscale) or
`curl http://localhost:8004/health` (on the Desktop).

Restart after changing flags or the model: `nssm restart voice-agent-llm`
(~10–20 s to reload the model on GPU).
