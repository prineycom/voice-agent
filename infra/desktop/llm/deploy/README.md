# LLM service deploy (NSSM)

Mirrors the STT/TTS/A2F NSSM setup (see [docs/DEPLOY.md](../../../../docs/DEPLOY.md),
[[voice-agent-deployment]]). Runs as LocalSystem, boot-start, auto-restart.

Unlike STT/TTS, this service runs `llama-server.exe` directly (no repo venv), so
it does **not** depend on the Desktop repo checkout — only on
`E:\AI\llama.cpp\bin` and the model on `E:\AI\models`.

```powershell
# one-time (elevated)
$nssm = "E:\voice-agent\tools\nssm.exe"

# Launch via the wrapper script so all flags stay in one place.
& $nssm install voice-agent-llm powershell.exe "-NoProfile -ExecutionPolicy Bypass -File E:\voice-agent\desktop\llm\start_llm.ps1"
& $nssm set voice-agent-llm AppDirectory "E:\voice-agent\desktop\llm"
& $nssm set voice-agent-llm Start SERVICE_AUTO_START
& $nssm set voice-agent-llm AppStdout "E:\voice-agent\desktop\llm\nssm-out.log"
& $nssm set voice-agent-llm AppStderr "E:\voice-agent\desktop\llm\nssm-err.log"
& $nssm start voice-agent-llm

netsh advfirewall firewall add rule name=voiceagent-llm dir=in action=allow protocol=TCP localport=8004
```

Health: `curl http://100.75.88.35:8004/health` (from the Pi, over Tailscale) or
`curl http://localhost:8004/health` (on the Desktop).

The launch script (`start_llm.ps1`) lives in the repo under
`infra/desktop/llm/`; the runtime copy is at `E:\voice-agent\desktop\llm\`.
Restart after changing flags or the model: `nssm restart voice-agent-llm`
(~10–20 s to reload the model on GPU).
