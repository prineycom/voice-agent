# Productionize the A2F (Audio2Face-3D) helper backend — implementation plan

**Task:** Ship the real A2F facial-animation helper as an always-on, reproducible service (finish Epic 8). Created from a `/grill` session.
**Complexity:** medium
**Mode:** sub-agents (orchestrator-driven; live infra over SSH/WSL)
**Parallel:** partial

## Goal

Bring the **real** A2F blendshape pipeline to production so that on `ai.priney.com`
the Live2D face animates from actual speech (not the volume-lipsync fallback):
Desktop TTS fork → **A2F helper service (:8003)** → agent worker → web Live2D.
The pipeline code is already merged and the agent worker already runs it; the only
missing piece is a **production, always-on, reproducible A2F service**.

## What is ALREADY done (do NOT rebuild — verified live)

- **`a2f_stream` C++ helper**: built, persistent (engine loaded once, N utterances),
  validated on the RTX 4070 (issue #34): 2 utterances → [28,28]×68 ARKit coeffs,
  ~1461 ms engine load, ~81 ms/utterance, **VRAM Δ ≈ 403 MiB**. `ldd` inside
  `nvcr.io/nvidia/tensorrt:25.08-py3` = **ALL_RESOLVED** (CUDA13/TRT10/glibc satisfied
  by the base image; the binary needs glibc 2.38 which the WSL host 22.04 lacks — so
  it MUST run in the container, which is the whole design).
- **`HelperBackend`** (`infra/desktop/a2f/engine.py`): persistent subprocess, lazy
  spawn, `asyncio.Lock`, respawn-on-crash. Reads `A2F_HELPER`, `A2F_MODEL_JSON`.
- **`server.py`** FastAPI (`WS /a2f`, `GET /health`), tests 6× green (GPU-free).
- **Artifacts on the box** (`~/a2f-sdk/` in WSL2 Ubuntu):
  - `a2f_stream/a2f_stream` (binary)
  - `Audio2Face-3D-SDK/_build/release/audio2x-sdk/lib/libaudio2x.so`
  - James model dir `.../generated/audio2face-sdk/samples/data/james/` with
    `model.json` → `network.trt` (160 MB), `implicit_emo_db.npz`, `model_data.npz`,
    `bs_skin.npz`/`bs_tongue.npz` + configs. **`network.onnx` (159 MB) is NOT needed
    at runtime** (only the built `.trt`).
- **Frontend** (`infra/pi/web/static/js/blendshapes.js|facial.js|mouth.js`): consumes
  blendshapes on the `voiceagent` DataChannel, A2F primary + volume fallback, wired
  by default. No flag needed to enable.
- **Agent worker**: restarted onto the A2F-forward code (tts_plugin.py forwards
  `{"type":"blendshapes"}`); `TTS_STREAMING` default True. No agent env needed.

## Fixed decisions (from grill)

- **Runtime:** run the A2F service inside the **`nvcr.io/nvidia/tensorrt:25.08-py3`**
  container with **CDI GPU** (`--device nvidia.com/gpu=all`).
- **Packaging:** a **reproducible Docker image built from a Dockerfile** (not a
  `docker commit`, not the ad-hoc `friendly_greider` container). `restart=always`.
- **Artifacts:** **bake** the prebuilt artifacts into the image (no SDK rebuild):
  the James model dir (minus `network.onnx`), `a2f_stream`, `libaudio2x.so`, plus
  `server.py`/`engine.py` + pip deps.
- **Always-on:** a **Windows NSSM service `voice-agent-a2f`** (LocalSystem, boot-start)
  wrapping `wsl -d Ubuntu -- docker …`, mirroring STT/TTS/LLM supervision.
- **Acceptance:** end-to-end face on the domain · reproducible one-command rebuild ·
  VRAM no OOM (STT+TTS+A2F). Reboot-survival is built (NSSM) but not gated on a forced
  reboot.

## Tasks

### Task 1: Stage artifacts + author the Dockerfile (repo)

- **Files:** `infra/desktop/a2f/deploy/Dockerfile` (create), `infra/desktop/a2f/deploy/build_image.sh` (create)
- **Depends on:** none
- **Scope:** M
- **What:** A Dockerfile + a build script that stages the prebuilt artifacts into a
  build context and produces image `voice-agent-a2f:latest`.
- **How:**
  - `build_image.sh` (runs in WSL2 Ubuntu): copy into a context dir `./ctx/`:
    `a2f_stream`, `libaudio2x.so`, the James model dir **excluding `network.onnx`**
    (`rsync --exclude network.onnx`), and the repo's `server.py`/`engine.py`/`requirements.txt`.
    Then `docker build -t voice-agent-a2f:latest`.
  - `Dockerfile`: `FROM nvcr.io/nvidia/tensorrt:25.08-py3`; `COPY` model dir →
    `/opt/a2f/model/`, `a2f_stream` → `/opt/a2f/a2f_stream` (chmod +x),
    `libaudio2x.so` → `/opt/a2f/lib/` with `ENV LD_LIBRARY_PATH=/opt/a2f/lib:/usr/local/cuda/lib64`;
    `COPY` service code → `/app`; `pip install -r requirements.txt`;
    `ENV A2F_BACKEND=helper A2F_HELPER=/opt/a2f/a2f_stream A2F_MODEL_JSON=/opt/a2f/model/model.json A2F_HOST=0.0.0.0 A2F_PORT=8003`;
    `EXPOSE 8003`; `CMD ["python3","/app/server.py"]` (or uvicorn).
  - Confirm `a2f_stream`'s rpath vs `LD_LIBRARY_PATH`: the binary was linked
    `-rpath $LIB` (the SDK build path, absent in image) — the `LD_LIBRARY_PATH` above
    covers `libaudio2x.so`; CUDA/TRT come from the base image.
- **Context:** artifact paths above; `infra/desktop/a2f/requirements.txt`, `server.py`, `engine.py`.
- **Verify:** `docker build` succeeds; image lists in `docker images`.

### Task 2: Bring the container up + smoke-test the helper on GPU

- **Depends on:** Task 1
- **Scope:** M
- **What:** Run the image with GPU, confirm `/health` = `helper` and blendshapes flow.
- **How:** `docker run -d --restart always --device nvidia.com/gpu=all -p 8003:8003 --name voice-agent-a2f voice-agent-a2f:latest`.
  Wait for engine load; `curl http://127.0.0.1:8003/health` → `{"backend":"helper"}`.
  Drive `WS /a2f` with a short PCM16@24k utterance (reuse a test WAV / the a2f tests'
  fixture), assert `{"type":"blendshapes",...}` frames + `{"done":true}`. Capture
  `nvidia-smi` before/after (expect Δ≈0.4 GB, STT+TTS still resident, no OOM).
- **Context:** `infra/desktop/a2f/tests/` for the wire format; spike doc for expected frame counts.
- **Verify:** health=helper; ≥1 utterance yields 68-coeff frames; VRAM within budget.

### Task 3: Windows→WSL port bridge check

- **Depends on:** Task 2
- **Scope:** S
- **What:** Confirm the Windows TTS service can reach the container on `127.0.0.1:8003`.
- **How:** From Windows PowerShell: `curl http://127.0.0.1:8003/health` (WSL2
  localhostForwarding). If it fails, set `.wslconfig` `localhostForwarding=true` or
  bind via the WSL IP; document the resolution.
- **Context:** memory `desktop-a2f-nim-wsl`.
- **Verify:** Windows-side curl to `127.0.0.1:8003/health` returns ok.

### Task 4: NSSM always-on service `voice-agent-a2f`

- **Files:** `infra/desktop/a2f/deploy/run_a2f.ps1` (create), `infra/desktop/a2f/deploy/install_nssm_a2f.ps1` (create), `infra/desktop/a2f/deploy/README.md` (edit)
- **Depends on:** Task 2
- **Scope:** M
- **What:** Boot-start NSSM service that keeps the container running via WSL.
- **How:** `run_a2f.ps1`: ensure docker up in WSL (`wsl -d Ubuntu -- sh -c "service docker start 2>/dev/null; docker start -a voice-agent-a2f"`), foreground so NSSM supervises; on first run create the container if absent. `install_nssm_a2f.ps1` (idempotent, mirrors `install_nssm_llm.ps1`): `nssm install voice-agent-a2f powershell -File run_a2f.ps1`, `Start SERVICE_AUTO_START`, logs, `nssm start`. Firewall rule optional (loopback only). Update `deploy/README.md` to the container reality (supersede the stale mock/NSSM-venv recipe).
- **Context:** `infra/desktop/llm/deploy/install_nssm_llm.ps1` (pattern), `infra/desktop/a2f/deploy/README.md`.
- **Verify:** `nssm status voice-agent-a2f` = SERVICE_RUNNING; `docker ps` shows the container; `/health`=helper after a service restart.

### Task 5: Enable the TTS→A2F fork

- **Files:** `infra/desktop/tts/.env` (live, on the box) + document in `.env.example` if needed
- **Depends on:** Task 3
- **Scope:** S
- **What:** Turn on the fork so TTS audio drives A2F.
- **How:** In the Desktop TTS service `.env`: `A2F_FORK_ENABLED=1`,
  `A2F_WS_URL=ws://127.0.0.1:8003/a2f`. `nssm restart voice-agent-tts`. Confirm TTS
  health still green and it connects to A2F (TTS log).
- **Context:** `infra/desktop/tts/a2f_fork.py`, `infra/desktop/tts/.env.example`.
- **Verify:** TTS log shows A2F fork connected; a synthesized utterance produces A2F frames.

### Task 6: Repo integration + docs

- **Files:** `docs/adr/00XX-a2f-helper-production.md` (create), `infra/desktop/a2f/README.md` (edit — unstale the status), `docs/DEPLOY.md` (edit — add `voice-agent-a2f`), `CLAUDE.md` (edit)
- **Depends on:** Task 4, Task 5
- **Scope:** M
- **What:** Record the containerized helper deploy + refresh stale docs.
- **How:** ADR: containerized A2F helper on the 4070 (image, NSSM+WSL supervision,
  bake-artifacts, VRAM). Fix `infra/desktop/a2f/README.md` stale `[ ]` checklist.
  Add the `voice-agent-a2f` row to DEPLOY.md. CLAUDE.md non-obvious note. Memory note.
- **Verify:** docs match the live deploy (paths, ports, image name).

### Task 7: End-to-end acceptance on the domain

- **Depends on:** all
- **Scope:** M
- **What:** Verify the three acceptance criteria.
- **How:**
  1. **End-to-end face:** open `ai.priney.com`, hold a short conversation; confirm the
     Live2D face animates from A2F blendshapes (not volume fallback — check the frames
     arrive on the `voiceagent` channel / `window.__a2f*` debug; `?lipsync=volume`
     only for comparison).
  2. **VRAM no OOM:** `nvidia-smi` with STT+TTS+A2F resident during a live reply.
  3. **Reproducibility:** re-run `build_image.sh` from clean → image rebuilds; NSSM
     restart brings the service back green.
- **Verify:** face animates live on the domain; VRAM in budget; one-command rebuild works.

## Execution

- **Order:**
  - Task 1 → Task 2 (build → run+smoke)
  - ─── barrier ───
  - Task 3, Task 4 (port bridge ∥ NSSM install)
  - ─── barrier ───
  - Task 5 (enable TTS fork)
  - ─── barrier ───
  - Task 6, Task 7 (docs ∥ acceptance)

## Verification (acceptance criteria)

1. Live Live2D face animated by real A2F blendshapes on `ai.priney.com` — Task 7.
2. Reproducible: `build_image.sh` rebuilds the image from staged artifacts; NSSM
   restart recovers the service — Task 1, Task 4, Task 7.
3. VRAM no OOM with STT+TTS+A2F co-resident (A2F Δ≈0.4 GB) — Task 2, Task 7.

## Materials

- Prior work — issue #34 (`.yoke/ai/34-persistent-a2f-stream/`), #33/#35/#36; `docs/research/2026-07-03-a2f-3d-spike.md`; ADR 0012/0013.
- Code — `infra/desktop/a2f/{server.py,engine.py,a2f_stream/,build_engine.sh,build_helper.sh,requirements.txt}`; `infra/desktop/tts/a2f_fork.py`; `infra/pi/web/static/js/{blendshapes,facial,mouth}.js`.
- Artifacts (WSL2 `~/a2f-sdk/`) — `a2f_stream/a2f_stream`, `…/audio2x-sdk/lib/libaudio2x.so`, `…/samples/data/james/` (model dir).
- Deploy pattern — `infra/desktop/llm/deploy/install_nssm_llm.ps1`.
- Memory — `desktop-a2f-nim-wsl`, `voice-agent-deployment`, `ai-priney-com-ingress`.

## Human prerequisites / risks

- **Desktop up + WSL2 Ubuntu running**, Docker daemon in WSL, CDI GPU
  (`nvidia.com/gpu=all`) — currently working (container Up 43 h). **Regenerate the
  CDI spec if the Windows GPU driver was updated** (`nvidia-ctk cdi generate --mode=wsl`).
- Artifacts live in the WSL2 filesystem — the image build depends on them being present
  (they are). They are NOT in git (160 MB engine); the Dockerfile bakes them from the
  staging copy.
- `network.onnx` must be excluded from the build context (159 MB, runtime-unused).
