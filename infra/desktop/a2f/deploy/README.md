# A2F service deploy (NSSM)

Mirrors the STT/TTS NSSM setup (see docs/DEPLOY.md, [[voice-agent-deployment]]).
Runs as LocalSystem, boot-start, auto-restart, from the repo checkout venv.

```powershell
# one-time (elevated)
$nssm = "E:\voice-agent\tools\nssm.exe"
$app  = "E:\voice-agent-repo\infra\desktop\a2f\.venv\Scripts\python.exe"
& $nssm install voice-agent-a2f $app "E:\voice-agent-repo\infra\desktop\a2f\server.py"
& $nssm set voice-agent-a2f AppDirectory "E:\voice-agent-repo\infra\desktop\a2f"
& $nssm set voice-agent-a2f Start SERVICE_AUTO_START
& $nssm set voice-agent-a2f AppEnvironmentExtra "HF_HOME=E:\AI\models" "HF_HUB_DISABLE_XET=1"
& $nssm start voice-agent-a2f

netsh advfirewall firewall add rule name=voiceagent-a2f dir=in action=allow protocol=TCP localport=8003
```

Health: `curl http://100.75.88.35:8003/health`.

Note: the `helper` backend additionally needs the compiled `a2f_stream` binary +
`network.trt` + `model.json` under `/opt/a2f` (WSL2) and the CUDA/TensorRT runtime
libs on PATH. Until the helper lands, the service runs `A2F_BACKEND=mock`.
