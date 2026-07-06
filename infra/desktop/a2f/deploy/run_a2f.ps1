# Foreground launcher for the A2F helper container, supervised by NSSM
# (voice-agent-a2f). NSSM keeps THIS process alive; if the container or WSL exits,
# NSSM restarts it. The A2F service must run in the TensorRT container (the
# a2f_stream binary needs glibc 2.38/CUDA13/TRT10 the WSL host lacks) — see
# deploy/README.md and the ADR.
#
# Runs `docker run` attached (not detached): NSSM = the supervisor, so no
# --restart policy is needed. Publishes 8003 to the WSL host; WSL2
# localhostForwarding exposes it to Windows 127.0.0.1:8003 (the TTS fork target).

$ErrorActionPreference = "Continue"

# Ensure the docker daemon is up in WSL, drop any stale container, then run
# attached so NSSM supervises. `exec` so signals reach docker cleanly.
$inner = @'
(systemctl start docker 2>/dev/null || service docker start 2>/dev/null || true)
for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done
docker rm -f voice-agent-a2f 2>/dev/null || true
exec docker run --rm --name voice-agent-a2f --device nvidia.com/gpu=all -p 8003:8003 voice-agent-a2f:latest
'@

wsl -d Ubuntu -- bash -lc $inner
