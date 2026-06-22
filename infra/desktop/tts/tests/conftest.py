"""Test fixtures for the TTS service.

Puts the service dir on sys.path (so `import server`/`import synthesize`/
`from audio import ...` resolve regardless of cwd) and stubs the GPU-only
`faster_qwen3_tts` dependency so the modules import without CUDA / the model.
"""

import sys
import types
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent.parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

if "faster_qwen3_tts" not in sys.modules:
    stub = types.ModuleType("faster_qwen3_tts")

    class _StubFasterQwen3TTS:  # pragma: no cover - replaced per-test
        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            return cls()

        def generate_custom_voice_streaming(self, *args, **kwargs):
            return iter(())

    stub.FasterQwen3TTS = _StubFasterQwen3TTS
    sys.modules["faster_qwen3_tts"] = stub
