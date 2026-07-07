#!/usr/bin/env bash
# Build the voice-agent-a2f Docker image from the prebuilt spike artifacts +
# the FastAPI service code. Reproducible: re-run to rebuild from the same inputs.
#
# Run in WSL2 Ubuntu on the Desktop:
#   bash /mnt/e/voice-agent-repo/infra/desktop/a2f/deploy/build_image.sh
#
# The heavy artifacts (a2f_stream, libaudio2x.so, the James model dir) are treated
# as prebuilt inputs (see issue #34 / the spike) — this does NOT rebuild the SDK.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"     # infra/desktop/a2f/deploy
A2F="$(dirname "$HERE")"                    # infra/desktop/a2f

# Artifact sources (override via env if the SDK moved)
SDK="${SDK:-$HOME/a2f-sdk}"
JAMES="${JAMES:-$SDK/Audio2Face-3D-SDK/_data/generated/audio2face-sdk/samples/data/james}"
A2E="${A2E:-$SDK/Audio2Face-3D-SDK/_data/generated/audio2emotion-sdk/samples/model}"
LIBA="${LIBA:-$SDK/Audio2Face-3D-SDK/_build/release/audio2x-sdk/lib/libaudio2x.so}"
BIN="${BIN:-$SDK/a2f_stream/a2f_stream}"

# Stage the build context in the WSL-native FS (NOT /mnt/e — 9p is slow for docker)
CTX="${CTX:-$HOME/a2f-image-ctx}"

for f in "$JAMES/model.json" "$JAMES/network.trt" \
         "$A2E/model.json" "$A2E/model_config.json" "$A2E/network.trt" "$A2E/network_info.json" \
         "$LIBA" "$BIN"; do
  [ -e "$f" ] || { echo "MISSING artifact: $f" >&2; exit 1; }
done

echo "== staging context at $CTX =="
rm -rf "$CTX"; mkdir -p "$CTX/model" "$CTX/a2e" "$CTX/app"
# model dir minus the runtime-unused ONNX (~159 MB)
rsync -a --exclude 'network.onnx' "$JAMES/" "$CTX/model/"
# A2E (audio2emotion) model dir; model.json references only network.trt, so the
# ONNX (~1.27 GB) is runtime-unused — exclude it, same as the James model above
rsync -a --exclude 'network.onnx' "$A2E/" "$CTX/a2e/"
cp "$BIN"  "$CTX/a2f_stream"
cp "$LIBA" "$CTX/libaudio2x.so"
cp "$A2F"/server.py "$A2F"/engine.py "$A2F"/emotion.py "$A2F"/arkit.py "$A2F"/requirements.txt "$CTX/app/"
cp "$HERE/Dockerfile" "$CTX/Dockerfile"

echo "== docker build voice-agent-a2f:latest =="
docker build -t voice-agent-a2f:latest "$CTX"
echo "== done: voice-agent-a2f:latest =="
docker images voice-agent-a2f:latest
