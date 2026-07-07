#!/usr/bin/env bash
# Compile the a2f_stream helper against the built libaudio2x + SDK headers.
# Run inside the TRT toolchain container (nvcr.io/nvidia/tensorrt:25.08-py3) with
# the SDK checkout mounted. Requires ./build.sh to have produced libaudio2x.so.
set -euo pipefail
SDK=${SDK:-/root/a2f-sdk/Audio2Face-3D-SDK}
SRC=${SRC:-$(dirname "$0")/a2f_stream/main.cpp}
OUT=${OUT:-$(dirname "$0")/a2f_stream/a2f_stream}
LIB="$SDK/_build/release/audio2x-sdk/lib"

g++ -std=c++17 -O2 "$SRC" -o "$OUT" \
  -I"$SDK/audio2face-sdk/include" \
  -I"$SDK/audio2emotion-sdk/include" \
  -I"$SDK/audio2x-common/include" \
  -I/usr/local/cuda/include \
  -L"$LIB" -laudio2x \
  -L/usr/local/cuda/lib64 -lcudart \
  -Wl,-rpath,"$LIB" -Wl,-rpath,/usr/local/cuda/lib64
echo "built: $OUT"
