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

EXPOSE 8003
CMD ["python3", "/app/server.py"]
