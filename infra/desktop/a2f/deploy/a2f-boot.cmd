@echo off
REM Boot the A2F helper container after user logon (WSL2 cannot run under
REM LocalSystem, so this runs in the user session via a logon Scheduled Task).
REM The container itself is `--restart always`, so this only needs to ensure WSL
REM + dockerd are up and the container is started.
wsl.exe -d Ubuntu -- bash -lc "(systemctl start docker 2>/dev/null || service docker start 2>/dev/null || true); for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done; docker start voice-agent-a2f"

REM Keep the WSL2 VM warm 24/7. Without a long-lived Windows-side wsl.exe session
REM the VM idles down (even with systemd+dockerd running) and takes the
REM voice-agent-a2f container down with it. This used to be provided by ACCIDENT
REM by an orphan a2f_stream spike container; when that was pruned the VM began
REM idling out. Launch the keepalive fully DETACHED via WMI (Win32_Process.Create)
REM so it survives after this boot script and its scheduled-task process tree exit.
powershell -NoProfile -Command "Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='C:\Windows\System32\wsl.exe -d Ubuntu -u root -- sleep infinity'} | Out-Null"
