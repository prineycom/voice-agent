$ErrorActionPreference = "Continue"
$nssm = "E:\voice-agent\tools\nssm.exe"
$repo = "E:\voice-agent-repo\infra\desktop\llm"

# Remove any prior instance so this is idempotent.
& $nssm stop voice-agent-llm 2>$null
& $nssm remove voice-agent-llm confirm 2>$null
Start-Sleep 2

& $nssm install voice-agent-llm "powershell.exe" "-NoProfile -ExecutionPolicy Bypass -File $repo\start_llm.ps1"
& $nssm set voice-agent-llm AppDirectory "$repo"
& $nssm set voice-agent-llm Start SERVICE_AUTO_START
& $nssm set voice-agent-llm AppStdout "$repo\nssm-out.log"
& $nssm set voice-agent-llm AppStderr "$repo\nssm-err.log"
& $nssm set voice-agent-llm DisplayName "Voice Agent LLM (llama.cpp Qwen3.5-4B-MTP)"
& $nssm start voice-agent-llm
Start-Sleep 2

# Firewall (idempotent-ish; ignore if the rule already exists)
netsh advfirewall firewall delete rule name=voiceagent-llm 2>$null | Out-Null
netsh advfirewall firewall add rule name=voiceagent-llm dir=in action=allow protocol=TCP localport=8004 | Out-Null

Write-Output "=== service status ==="
& $nssm status voice-agent-llm
