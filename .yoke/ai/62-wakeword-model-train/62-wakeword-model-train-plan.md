# Plan: 62-wakeword-model-train

**Ticket:** #62 — Wake-word model: train the custom Приней/Приня/хей джарвис VoxCPM2 ONNX (GPU + user voice)
**Mode:** operational-runbook (not code sub-agents — see Note)
**Parallel:** no (strictly sequential; several human/GPU gates)

## Note — why this is a runbook, not the usual code pipeline

The whole wake-word **software** pipeline (#58–#60) is merged and live. Issue #62
is the one remaining acceptance item: actually **train** the custom Russian model
on the Desktop RTX 4070 and prove it wakes the agent. So the "tasks" below are
operational steps the orchestrator runs directly over SSH (`server: desktop`) plus
local Pi edits — there is exactly one code/artifact change to commit
(`prinei.onnx` + `thresholds.json`). It is long-running (hours of GPU) and
collaborative: three steps need you (Pavel) directly, marked **👤 YOU**.

## Live facts (probed 2026-07-13, this session)

- **GPU:** 4353 MiB free / 7660 MiB held by prod `voice-agent-stt` + `voice-agent-tts`
  (NSSM). Stopping both → ~11.6 GB free (VoxCPM2 fp16 ~5.7 GB + training fits the 12 GB 4070).
- **A2F:** Docker container inside the running Ubuntu WSL, ~0.4 GB — negligible, left running.
- **VoxCPM2 snapshot (reuse, no re-download):**
  `E:\AI\models\hub\models--openbmb--VoxCPM2\snapshots\bffb3df5a29440629464e5e839f4d214c8714c3d`
  (has `model.safetensors`, `config.json`, `audiovae.pth`, tokenizer). `.venv-voxcpm` also exists.
- **Missing on Desktop:** `uv`, a `livekit-wakeword` checkout, and the repo's `wakeword/`
  dir (the Desktop repo checkout is stale). git 2.45 present; ~92 GB free on E:.
- **Repo:** model binaries are gitignored (`infra/desktop/.gitignore` → `models/`, `*.wav`).
  `prinei.onnx` + `thresholds.json` must be `git add -f`. Pi `config.py` defaults the model to
  `hey_jarvis.onnx`; adding `prinei` = set `WAKEWORD_MODEL_PATHS` to both files. Threshold lookup
  is by **ONNX filename stem** — the `thresholds.json` key must equal the saved filename's stem.

## Key decisions for the confirmation gate

- **DD-1 — Training size / wall-clock.** `prinei-prod.yaml` is set to `n_samples: 50000`
  (+10k val), `steps: 100000`. VoxCPM2 streams at ~RTF 1.8; 50k short syntheses (even batched
  at 50) is realistically **many hours → overnight/multi-day** of GPU synthesis before the 100k
  training steps. Options at the gate: **(A)** run 50k as-authored (best recall, longest), or
  **(B)** a **first pass at ~10–15k** to validate the pipeline end-to-end and get a working model
  fast, then scale to 50k only if real-user recall is weak (the README's documented lever).
  Recommendation: **B** — de-risks the toolchain (uv/torch/VoxCPM install, `setup` downloads)
  before committing to the long run, and short Russian names may already work at 10–15k.
- **DD-2 — When services go down.** During `run` the voice agent is **offline** (STT+TTS
  stopped). You authorized stopping them. Confirm timing: start now, or schedule off-hours.
- **DD-3 — Snapshot reuse.** Set `voxcpm_tts.local_model_path` to the snapshot dir above
  (or `HF_HUB_CACHE=E:\AI\models\hub` so the cached repo id resolves) — no 4.6 GB re-download.

## Tasks

### T1 — Prep Desktop training env
- Install `uv` (standalone, no admin): `irm https://astral.sh/uv/install.ps1 | iex`.
- `git clone https://github.com/livekit/livekit-wakeword E:\livekit-wakeword`.
- `uv sync --extra train --extra voxcpm` (torch-CUDA + VoxCPM2 deps). Risks: HF Xet hang →
  `HF_HUB_DISABLE_XET=1`; transformers version pin vs VoxCPM2. Verify torch sees CUDA.
- Copy repo `configs/prinei-prod.yaml` into the checkout; set `local_model_path` to the snapshot;
  apply DD-1 `n_samples` if option B.
- **Verify:** `uv run python -c "import torch; print(torch.cuda.is_available())"` → True.

### T2 — Free the GPU  👤 (your go-ahead from DD-2)
- `nssm stop voice-agent-stt` + `nssm stop voice-agent-tts`. Confirm `nvidia-smi` free ≥ ~11 GB.
- **Verify:** free VRAM ≥ 11 GB; agent expected offline (documented, temporary).

### T3 — Prefetch + train (long-running, detached)
- `livekit-wakeword setup --config prinei-prod.yaml` (backgrounds/RIRs + VoxCPM2 check).
- `livekit-wakeword run prinei-prod.yaml` — launched **detached** (WMI `Win32_Process.Create`,
  per memory `desktop-ssh-long-running-procs`, since the MCP SSH session caps ~5 min and would
  kill a child). Log to a file; poll periodically.
- **Verify:** `output/prinei_ru/prinei_ru.onnx` produced; training log shows converged loss.

### T4 — Evaluate + pick threshold
- `livekit-wakeword eval prinei-prod.yaml` → DET curve / AUT / FPPH / **optimal threshold**.
- **Verify:** record the numbers; capture the recommended cutoff for T6.

### T5 — Restore Desktop services
- `nssm start voice-agent-stt` + `nssm start voice-agent-tts`; confirm both `Running` and the
  live agent is back. (Independent of remaining steps — do it as soon as train+eval are done.)
- **Verify:** services Running; a quick browser smoke that STT/TTS respond.

### T6 — Land the model in the repo
- `ssh_download` `output/prinei_ru/prinei_ru.onnx` → `infra/desktop/wakeword/models/prinei.onnx`.
- Run `detect.py` once to learn the key `predict()` emits for it; set `thresholds.json`'s key
  (stem-matched) to T4's optimal cutoff (fallback 0.5, README caveat).
- `git add -f infra/desktop/wakeword/models/prinei.onnx infra/desktop/wakeword/models/thresholds.json`
  (also force-add `hey_jarvis.onnx` for a reproducible gate). Commit
  `#62 feat(62-wakeword-model-train): add trained Приней/Приня/хей джарвис ONNX + threshold`.
- **Verify:** `git ls-files` shows the model + threshold tracked.

### T7 — Record acceptance clips  👤 **YOU**
- You record 16 kHz mono WAVs of your own voice: `pos_prinei.wav`, `pos_prinya.wav`,
  `pos_hey_jarvis.wav` (`arecord -r 16000 -c1 -f S16_LE …` on the Pi, or I'll give a recorder).
- Run `detect.py -m hey_jarvis.onnx prinei.onnx -t <thr> pos_*.wav` — all three must HIT.
- A 2–3 min general-Russian clip (or live talk) must stay silent (`neg_*`).
- **Verify:** self-check passes (pos_* fire, neg_* silent); tune `thresholds.json` if a name misses.

### T8 — Deploy to Pi + live acceptance  👤 (final browser check with YOU)
- Set `WAKEWORD_MODEL_PATHS=<repo>/infra/desktop/wakeword/models/hey_jarvis.onnx,…/prinei.onnx`
  and `WAKEWORD_THRESHOLDS_PATH=…/thresholds.json` in `infra/pi/agent/.env`; redeploy the Pi worker.
- Optionally `WAKEWORD_DEBUG=1` to watch live scores while confirming.
- **Verify (acceptance):** in the browser, `Приней` / `Приня` / natural-Russian `хей джарвис`
  each wake the agent; a 2–3 min normal Russian conversation does not.

### T9 — Validation (docs/report)
- Update `README.md` status table (Приней/Приня → trained), note eval numbers + final threshold.
- Write the execution report; commit env-example note if `WAKEWORD_MODEL_PATHS` guidance changed.

## Verification (acceptance criteria, from #62)

- [ ] `Приней` / `Приня` / natural-Russian `хей джарвис`, spoken by Pavel, reliably wake the agent.
- [ ] A 2–3 min general-Russian conversation does not trip the detector.
- [ ] `prinei.onnx` + its threshold committed; the retrain recipe still reproduces it.
- [ ] Inference cost re-confirmed cheap enough for the always-on Pi gate (~83 ms/3-model window).

## Execution

- **Order:** T1 → T2(👤 go) → T3 → T4 → T5 → T6 → T7(👤) → T8(👤) → T9. Strictly sequential.
- **Reasoning:** each step gates the next (env before train, GPU free before run, model before
  acceptance); three steps need Pavel directly; the run is multi-hour and detached.
