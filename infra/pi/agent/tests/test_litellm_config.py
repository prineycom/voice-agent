"""Offline validity check for the LiteLLM proxy config (issue #13).

Parses infra/pi/litellm/config.yaml and asserts the model-swap point holds
the expected provider/transport/endpoint values. No network.
"""

from pathlib import Path

import yaml

# This file: infra/pi/agent/tests/test_litellm_config.py
#   parents[0]=tests, parents[1]=agent, parents[2]=pi
# Config:    infra/pi/litellm/config.yaml
CONFIG_PATH = Path(__file__).resolve().parents[2] / "litellm" / "config.yaml"


def test_litellm_model_swap_point():
    assert CONFIG_PATH.exists(), CONFIG_PATH
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

    entry = data["model_list"][0]
    assert entry["model_name"] == "qwen3.5-4b"

    params = entry["litellm_params"]
    # Local Qwen3.5-4B-MTP served by llama.cpp on the Desktop GPU (ADR 0014),
    # reached over Tailscale, replacing the former Ollama Cloud nemotron MVP (ADR 0004).
    assert params["model"] == "openai/qwen3.5-4b"
    assert params["api_base"] == "http://100.75.88.35:8004/v1"
    assert params["api_key"] == "os.environ/LLM_LOCAL_API_KEY"
