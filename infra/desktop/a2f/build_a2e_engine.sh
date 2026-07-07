#!/usr/bin/env bash
# Build the batch-1 Audio2Emotion TensorRT engine (a2e emotion vectors for the
# A2F helper — issue #40; the face-engine counterpart is build_engine.sh).
#
# Runs inside the TensorRT toolchain container (CUDA 13 + TRT 10.13). Requires:
#   - the Audio2Face-3D-SDK checked out at $SDK
#   - a HF read token at /root/hf.key (mounted read-only into the container)
#     with the nvidia/Audio2Emotion-v2.2 license accepted (HF-gated)
#
# Unlike build_engine.sh this script also downloads the model (~1 GB ONNX),
# since the a2e model was never fully fetched during the #34 spike.
#
# Usage (on the Desktop, in Ubuntu WSL2; run detached — download + build take
# minutes):
#   docker run -d --name a2e-build --device nvidia.com/gpu=all \
#     -v /root/a2f-sdk/Audio2Face-3D-SDK:/work -v /root/hf.key:/root/hf.key:ro \
#     -w /work nvcr.io/nvidia/tensorrt:25.08-py3 bash build_a2e_engine.sh
#   docker logs -f a2e-build    # then: docker rm a2e-build
set -euo pipefail
SDK=${SDK:-/work}
MODEL=audio2emotion-v2.2  # hardcoded in audio2emotion-sdk/scripts/common.py too
export TENSORRT_ROOT_DIR=/usr
export PATH="/opt/tensorrt/bin:/usr/src/tensorrt/bin:$PATH"
export PYTHONPATH="$SDK/audio2x-common/scripts:$SDK/audio2emotion-sdk/scripts:${PYTHONPATH:-}"
# Xet-backed HF downloads hang at 0 bytes on this box — force plain HTTP.
export HF_HUB_DISABLE_XET=1
cd "$SDK"

# hf CLI + numpy (imported by audio2x.data_utils); the TRT container has neither.
python3 -m pip install -q "huggingface_hub==0.34.3" "numpy>=1.26,<2"

export HF_TOKEN="${HF_TOKEN:-$(cat /root/hf.key)}"
hf auth whoami

# Clear orphaned .lock files left behind by an interrupted previous download.
find "_data/audio2emotion-models/$MODEL/.cache/huggingface" -name '*.lock' -delete 2>/dev/null || true

# Download the (HF-gated) model into the layout common.py expects.
mkdir -p _data/audio2emotion-models
hf download nvidia/Audio2Emotion-v2.2 --local-dir "_data/audio2emotion-models/$MODEL"

# Force batch=1, mirroring build_engine.sh. Harmless if the a2e shapes don't
# template a batch dim — the defaults only apply to {…} format vars.
python3 - "$MODEL" <<'PY'
import json, sys
p = f"_data/audio2emotion-models/{sys.argv[1]}/trt_info.json"
d = json.load(open(p)); d.setdefault("defaults", {})
d["defaults"]["MAX_BATCH_SIZE"] = 1
d["defaults"]["OPT_BATCH_SIZE"] = 1
json.dump(d, open(p, "w"), indent=4)
print("batch forced to 1:", d["defaults"])
PY

# Build the engine via the SDK's converter (trtexec under the hood). gen_data()
# also copies model.json + the config JSONs next to the engine.
python3 audio2emotion-sdk/scripts/gen_sample_data.py
echo "engine built"

OUT="_data/generated/audio2emotion-sdk/samples/model"
ls -la "$OUT"
echo "Engine dir: $OUT  (model.json + network.trt — bake these into the helper image)"
