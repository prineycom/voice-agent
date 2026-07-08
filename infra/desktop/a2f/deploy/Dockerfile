# Production A2F (Audio2Face-3D) helper service.
#
# The compiled a2f_stream binary needs glibc 2.38 / CUDA 13 / TensorRT 10 — all
# provided by this base image (verified: `ldd a2f_stream` = all resolved inside
# it). The WSL2 host (Ubuntu 22.04, glibc 2.35) cannot run it, so the whole
# service runs in this container with CDI GPU. See ADR / deploy/README.md.
#
# Build context is staged by build_image.sh (prebuilt spike artifacts + service
# code) — this Dockerfile only bakes them; it does NOT rebuild the SDK/engine.
FROM nvcr.io/nvidia/tensorrt:25.08-py3

# --- baked GPU artifacts (from build_image.sh ./ctx) --------------------------
# James model dir (model.json + network.trt + npz + configs; network.onnx excluded)
COPY model/          /opt/a2f/model/
# A2E (audio2emotion) model dir (model.json + network.trt + configs; network.onnx excluded)
COPY a2e/            /opt/a2f/a2e/
# Persistent C++ helper + its SDK runtime lib
COPY a2f_stream      /opt/a2f/a2f_stream
COPY libaudio2x.so   /opt/a2f/lib/libaudio2x.so
RUN chmod +x /opt/a2f/a2f_stream

# --- FastAPI service + deps ---------------------------------------------------
WORKDIR /app
COPY app/ /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# libaudio2x from /opt/a2f/lib; CUDA/TRT from the base image
ENV LD_LIBRARY_PATH=/opt/a2f/lib:/usr/local/cuda/lib64 \
    A2F_BACKEND=helper \
    A2F_HELPER=/opt/a2f/a2f_stream \
    A2F_MODEL_JSON=/opt/a2f/model/model.json \
    A2F_HOST=0.0.0.0 \
    A2F_PORT=8003

# --- A2E (audio2emotion) ------------------------------------------------------
# A2E engine load happens lazily on the first utterance, so the first-utterance
# timeout is raised from the default 30s (engine.py) to cover the TRT engine load.
# The A2E_* knobs below match the model-config values, spelled out so the tuning
# pass can override them at `docker run` without rebuilding.
# Other helper knobs (unset = model default, override at run time if needed):
#   A2E_MAX_EMOTIONS — the A2E network classifies only SIX emotions
#     (angry/disgust/fear/happy/neutral/sad), so the model default 6 already
#     means "no truncation". NEVER set >6: the SDK's CUDA post-process kernel
#     computes `emotionsToZero = 6 - maxEmotions` UNSIGNED, underflows, and
#     spins forever (helper produces no frames — verified live, #40 Task 8);
#     0 zeroes ALL output.
#   A2F_SKIN_STRENGTH, A2F_UPPER_FACE_STRENGTH, A2F_LOWER_FACE_STRENGTH,
#   A2F_BLINK_STRENGTH, A2F_BS_OFFSETS
ENV A2E_MODEL_JSON=/opt/a2f/a2e/model.json \
    A2E_ENABLED=1 \
    A2F_HELPER_FIRST_TIMEOUT=60 \
    A2E_EMOTION_STRENGTH=0.4 \
    A2E_EMOTION_CONTRAST=1.0 \
    A2E_LIVE_BLEND_COEF=0.7 \
    A2E_LIVE_TRANSITION_TIME=0.5 \
    A2E_PREFERRED_STRENGTH=0.6
# ^ EMOTION_STRENGTH 0.4 + PREFERRED_STRENGTH 0.6 are the winning config of the
# #45 streaming-helper emotion re-tuning (window stays at the code default
# A2E_BUFFER_LENGTH=16000 — larger windows batch A2E emission into >0.5 s frame
# bursts and fail streaming health). Restores the #40 criteria (a)-(d) on the
# streaming helper: preferred 0.6 lifts the joy/anger tag boost (b), strength
# 0.4 damps the A2E share enough for BrowDownLeft joy-mean < 0.15 (c) while the
# lower neutral baseline keeps (a) and the boost ratios green.

# --- Tuned emotion expressiveness (#40 Task 8) ---------------------------------
# Winning config of the bounded tuning pass (docs/research/data/
# 40-a2f-emotion-supply/analysis.md, "Tuning" section): all A2E knobs stay at
# the defaults above; per-pose brow gains lift the A2E baseline over the old
# zeros production (criterion a) while keeping the joy/anger boost criteria
# (b)-(d) green. Pose names are the SOLVER's camelCase names (bs_skin.npz),
# NOT the ARKit CamelCase the service emits — wrong names warn + no-op.
ENV A2F_BS_MULTIPLIERS="browInnerUp=1.35,browDownLeft=1.25,browDownRight=1.25"

EXPOSE 8003
CMD ["python3", "/app/server.py"]
