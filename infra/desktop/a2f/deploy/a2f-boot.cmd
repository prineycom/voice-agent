@echo off
REM Boot the A2F helper container after user logon (WSL2 cannot run under
REM LocalSystem, so this runs in the user session via a logon Scheduled Task).
REM The container itself is `--restart always`, so this only needs to ensure WSL
REM + dockerd are up and the container is started.
wsl.exe -d Ubuntu -- bash -lc "(systemctl start docker 2>/dev/null || service docker start 2>/dev/null || true); for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done; docker start voice-agent-a2f"
