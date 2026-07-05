# Desktop local LLM — llama.cpp (llama-server) serving Qwen3.5-4B-Q4_K_M (MTP)
# for the voice agent. OpenAI-compatible /v1 on port 8004, reached from the Pi
# agent through the LiteLLM proxy (alias `voice-agent`).
#
# Dedicated, always-on server — SEPARATE from the interactive llama.cpp tray
# server on :8080 (E:\AI\llama.cpp\tray) so switching models there never takes
# the agent's LLM down. See README.md and deploy/README.md.
#
# Non-thinking (enable_thinking=false) + MTP self-speculative decoding
# (--spec-type draft-mtp) for low voice latency. KV cache q8_0 and a reduced
# micro-batch keep VRAM inside the ~4 GB budget alongside STT+TTS(+A2F).

$ErrorActionPreference = "Continue"

$server = "E:\AI\llama.cpp\bin\llama-server.exe"
$model  = "E:\AI\models\qwen3.5\Qwen3.5-4B-Q4_K_M.gguf"
$logDir = "E:\voice-agent-repo\infra\desktop\llm"   # the git checkout the NSSM service runs from (git pull deploys updates)
$log    = "$logDir\server.log"

# --- tunables (finalized in the VRAM/context tuning step; see README.md) -------
# KV cache is q8_0 GQA on a 4B model, so it is tiny (~136 MiB @ 8k) — context is
# cheap. VRAM is dominated by weights (~2.7 GB) + two compute buffers (main + the
# MTP draft context). The micro-batch is the real VRAM lever, not context.
$ctx      = 16384    # generous conversation window; KV ~0.27 GB
$port     = 8004     # 8001=STT, 8002=TTS, 8003=A2F(reserved), 8004=LLM
$specNMax = 6        # MTP draft tokens per step
$batch    = 256      # logical batch
$ubatch   = 128      # micro-batch — small on purpose: each compute buffer scales
                     # with it (~245 MiB @256, ~123 MiB @128). Two contexts (main +
                     # MTP draft) each carry one, so trimming it buys ~0.5 GB of the
                     # co-tenancy headroom for A2F. Prefill of short voice prompts
                     # is unaffected in practice.

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# Non-thinking mode. Qwen3.5 honors the jinja `enable_thinking` kwarg (NOT
# --reasoning-budget, which the template ignores). Set it via the env var, not
# the --chat-template-kwargs CLI flag: PowerShell 5.1 strips the inner quotes
# when passing JSON to a native exe, corrupting the flag. The env var is read
# verbatim.
$env:LLAMA_CHAT_TEMPLATE_KWARGS = '{"enable_thinking":false}'

& $server `
    -m $model `
    --alias qwen3.5-4b `
    --host 0.0.0.0 --port $port `
    -ngl 99 `
    -c $ctx `
    -b $batch -ub $ubatch `
    -fa on `
    -ctk q8_0 -ctv q8_0 `
    --spec-type draft-mtp --spec-draft-n-max $specNMax `
    --jinja `
    -np 1 `
    *> $log 2>&1
