#!/usr/bin/env bash
# Redeploy the whole Voice Agent stack from git: Pi (worker + web) and the
# Desktop GPU services (STT + TTS). Pulls latest main on both, restarts services.
#
#   ./deploy.sh            # full redeploy (Pi + Desktop)
#   ./deploy.sh --pi       # Pi only
#   ./deploy.sh --desktop  # Desktop only
set -uo pipefail
REPO=/home/priney/repos/voice-agent
DESKTOP=Pavel@100.75.88.35
WHAT="${1:-all}"

if [[ "$WHAT" == "all" || "$WHAT" == "--pi" ]]; then
  echo "== [Pi] git pull =="
  git -C "$REPO" pull --ff-only
  echo "== [Pi] restart worker + web =="
  sudo systemctl restart voice-agent-worker voice-agent-web
  sleep 2
  for u in voice-agent-worker voice-agent-web; do printf "  %-22s %s\n" "$u" "$(systemctl is-active "$u")"; done
fi

if [[ "$WHAT" == "all" || "$WHAT" == "--desktop" ]]; then
  echo "== [Desktop] git pull + restart STT/TTS (reloads GPU models ~40s) =="
  ssh -o BatchMode=yes -o ConnectTimeout=10 "$DESKTOP" \
    "powershell -NoProfile -ExecutionPolicy Bypass -File E:\\voice-agent\\tools\\redeploy.ps1"
fi

echo "== [Pi] health =="
curl -fsS --max-time 5 http://127.0.0.1:8095/healthz >/dev/null 2>&1 && echo "  web(8095)   ok" || echo "  web(8095)   FAIL"
ss -ltn 2>/dev/null | grep -q ':8090' && echo "  worker(8090) ok" || echo "  worker(8090) FAIL"
echo "Done."
