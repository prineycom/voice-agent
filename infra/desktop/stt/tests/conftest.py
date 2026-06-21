"""Test fixtures for the STT service.

Puts the service dir on sys.path (so `import server`/`from audio import ...`
resolve regardless of cwd) and stubs the GPU-only `faster_whisper` dependency so
`server` can be imported on a machine without CUDA / the model installed.
"""

import sys
import types
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent.parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

if "faster_whisper" not in sys.modules:
    stub = types.ModuleType("faster_whisper")

    class _StubWhisperModel:  # pragma: no cover - replaced per-test
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            return iter(()), None

    stub.WhisperModel = _StubWhisperModel
    sys.modules["faster_whisper"] = stub
