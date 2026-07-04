#!/usr/bin/env bash
# Build the batch-1 Audio2Face TensorRT engine (the whole point — VRAM ~0.3 GB
# vs the NIM's ~8.8 GB; see docs/research/2026-07-03-a2f-3d-spike.md).
#
# Runs inside the TensorRT toolchain container (CUDA 13 + TRT 10.13). Requires:
#   - the Audio2Face-3D-SDK checked out at $SDK
#   - the James model downloaded (HF-gated) at $SDK/_data/audio2face-models/audio2face-3d-v2.3.1-james
#   - hf token + accepted licenses (nvidia/Audio2Face-3D-v2.3.1-James, nvidia/Audio2Emotion-v2.2)
#
# Usage (on the Desktop, in Ubuntu WSL2):
#   docker run --rm --device nvidia.com/gpu=all -v $SDK:/work -w /work \
#     nvcr.io/nvidia/tensorrt:25.08-py3 bash build_engine.sh
set -euo pipefail
SDK=${SDK:-/work}
MODEL=${A2F_MODEL:-audio2face-3d-v2.3.1-james}
export TENSORRT_ROOT_DIR=/usr
export PATH="/opt/tensorrt/bin:/usr/src/tensorrt/bin:$PATH"
export PYTHONPATH="$SDK/audio2x-common/scripts:$SDK/audio2face-sdk/scripts:${PYTHONPATH:-}"
cd "$SDK"

# Force batch=1: the model's trt_info.json defaults to MAX_BATCH_SIZE=128/OPT=8
# (multi-track cloud sizing). Overriding to 1 is what drops VRAM to ~0.3 GB.
python3 - "$MODEL" <<'PY'
import json, sys
p = f"_data/audio2face-models/{sys.argv[1]}/trt_info.json"
d = json.load(open(p)); d.setdefault("defaults", {})
d["defaults"]["MAX_BATCH_SIZE"] = 1
d["defaults"]["OPT_BATCH_SIZE"] = 1
json.dump(d, open(p, "w"), indent=4)
print("batch forced to 1:", d["defaults"])
PY

# Build the engine via the SDK's converter (trtexec under the hood).
python3 - "$MODEL" <<'PY'
import sys
import gen_sample_data as g
g.regression_gen_data(source_model=sys.argv[1], custom_folder_name="a2f")
print("engine built")
PY

ENGINE="_data/generated/audio2face-sdk/samples/data/a2f/network.trt"
ls -la "$ENGINE"
echo "Engine: $ENGINE  (copy to A2F_ENGINE, e.g. /opt/a2f/network.trt)"
