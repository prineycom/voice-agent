# Idempotent production setup for the A2F helper service.
#
# WSL2 cannot run under the LocalSystem account NSSM uses
# (WSL_E_LOCAL_SYSTEM_NOT_SUPPORTED), so the A2F container is NOT an NSSM service.
# Instead:
#   - the container runs with `--restart always` (survives crashes + WSL restarts)
#   - dockerd is enabled in WSL (starts with WSL)
#   - a logon Scheduled Task (a2f-boot.cmd) brings WSL + the container up in the
#     user session after login
# Headless reboot survival additionally requires Windows autologon for the desktop
# user (a machine prerequisite — see docs/DEPLOY.md / memory desktop-wol-autologon).
#
# Run elevated on the Desktop after build_image.sh has produced voice-agent-a2f:latest.

$ErrorActionPreference = "Continue"
$nssm    = "E:\voice-agent\tools\nssm.exe"
$bootCmd = "E:\voice-agent-repo\infra\desktop\a2f\deploy\a2f-boot.cmd"

# Remove any stale NSSM attempt (the LocalSystem approach cannot launch WSL).
& $nssm stop   voice-agent-a2f 2>&1 | Out-Null
& $nssm remove voice-agent-a2f confirm 2>&1 | Out-Null

# (Re)create the container: restart=always, CDI GPU, publish 8003.
$run = "docker rm -f voice-agent-a2f 2>/dev/null; " +
       "docker run -d --restart always --name voice-agent-a2f " +
       "--device nvidia.com/gpu=all -p 8003:8003 voice-agent-a2f:latest; " +
       "systemctl enable docker 2>&1 | tail -1"
wsl -d Ubuntu -- bash -lc $run

# Logon task to boot WSL + the container in the user session.
schtasks /Create /TN voice-agent-a2f-boot /TR $bootCmd /SC ONLOGON /RL HIGHEST /F

Start-Sleep 6
Write-Output "=== docker ps ==="
wsl -d Ubuntu -- docker ps
Write-Output "=== health (Windows -> WSL) ==="
cmd /c "curl.exe -s -m 4 http://127.0.0.1:8003/health"
