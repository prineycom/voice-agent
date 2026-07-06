# Idempotent NSSM install for the A2F helper service (voice-agent-a2f).
# LocalSystem, boot-start, auto-restart — mirrors STT/TTS/LLM. Runs run_a2f.ps1,
# which launches the containerized A2F service in WSL2. Run elevated.

$ErrorActionPreference = "Continue"
$nssm = "E:\voice-agent\tools\nssm.exe"
$repo = "E:\voice-agent-repo\infra\desktop\a2f\deploy"

& $nssm stop voice-agent-a2f 2>$null
& $nssm remove voice-agent-a2f confirm 2>$null
Start-Sleep 2

& $nssm install voice-agent-a2f "powershell.exe" "-NoProfile -ExecutionPolicy Bypass -File $repo\run_a2f.ps1"
& $nssm set voice-agent-a2f AppDirectory "$repo"
& $nssm set voice-agent-a2f Start SERVICE_AUTO_START
& $nssm set voice-agent-a2f AppStdout "$repo\nssm-out.log"
& $nssm set voice-agent-a2f AppStderr "$repo\nssm-err.log"
& $nssm set voice-agent-a2f DisplayName "Voice Agent A2F (Audio2Face-3D helper, WSL2 container)"
# Stop = kill the wrapper; the `--rm` container is torn down when docker run exits.
& $nssm set voice-agent-a2f AppStopMethodConsole 5000
& $nssm start voice-agent-a2f
Start-Sleep 2

Write-Output "=== service status ==="
& $nssm status voice-agent-a2f
