# Spike: CosyVoice2-0.5B for realtime expressive Russian TTS — implementation plan

**Task:** GitHub issue #47 (https://github.com/prineycom/voice-agent/issues/47)
**Complexity:** complex
**Mode:** sub-agents
**Parallel:** false

> **Nature of this work — read first.** This is a **human-gated GPU spike**, not a normal
> feature. The center of gravity is on the remote Windows Desktop (RTX 4070, 12 GB) under
> the MCP-SSH 30 s command cap, and the go/no-go hinges on **human listening verdicts** that
> no sub-agent can render. **The run cannot be fully AFK.** The prior VoxCPM2 spike
> (`docs/research/2026-07-09-voxcpm2-tts-spike.md:106-113`) passed a gate that *missed* the
> real killer — RTF measured standalone instead of through-the-service-with-A2F — and was
> rolled back same-day. This plan bakes that lesson into the technical gate (T9).
>
> Task **Location** tags: `remote-desktop` | `human-gate` | `local-repo`.
> Integration tasks (T10–T16) are **conditional** on gates T4 ∧ T6 ∧ T9 all passing green.
> Prod TTS stays on **Qwen** throughout — do not break it.

## Design decisions

### DD-1: CosyVoiceEngine copies the VoxCPMEngine shape

**Decision:** New `name = "cosyvoice"` engine, env-driven, `load()` + warmup, `stream_pcm()` generator, `health_fields()`.
**Rationale:** The registry is designed for exactly this (`infra/desktop/tts/engines.py:5-6` docstring: "new model = a new engine class + one line in `REGISTRY`"); `server.py`/`synthesize.py`/the WS protocol never change.
**Alternative:** Fork `VoiceCloneEngine` — rejected; VoxCPM (`engines.py:275`) is the closer analog (expressive + ref-clone + emotion).

### DD-2: Zero-shot clone uses BOTH prompt_speech + prompt_text

**Decision:** Neutral path calls CosyVoice2 `inference_zero_shot(tts_text, prompt_text=ref.ref_text, prompt_speech_16k=<ref audio>, stream=True)`.
**Rationale:** `_parse_refs` already yields WAV + transcript (`engines.py:150`); `ref_text`, dead weight for VoxCPM, is load-bearing here.
**Alternative:** Ignore `ref_text` like VoxCPM — rejected; CosyVoice2 zero-shot quality depends on the prompt transcript.

### DD-3: Emotion via inference_instruct2 + `<|endofprompt|>`, mapped from the 5-enum

**Decision:** Non-neutral emotion routes to `inference_instruct2(tts_text, instruct_text="<RU emotion word><|endofprompt|>", prompt_speech_16k=<ref>, stream=True)`; `neutral`/None → plain `inference_zero_shot`. New `COSYVOICE_EMOTION_PROMPTS = {"neutral":"", "happy":"радостно", "sad":"грустно", "surprised":"удивлённо", "thinking":"задумчиво"}`.
**Rationale:** Mirrors `VOXCPM_EMOTION_PROMPTS`/`_style_prefix` (`engines.py:266,321`), keyed on the exact strings the agent sends (`infra/pi/agent/tts_plugin.py:382` → `infra/pi/agent/motion_events.py:25`). instruct2 accepts a zero-shot `prompt_speech`, so cloning + emotion coexist.
**Alternative:** One always-instruct path — rejected; neutral is the common case and zero-shot is the higher-fidelity clone.
**Risk:** Whether instruct + Russian produce audible, correct emotion is **human gate T6**, not assumable.

### DD-4: Native 24 kHz; self._sr = 24000

**Decision:** `stream_pcm` yields `_emit_pcm(chunk, 24000)`.
**Rationale:** CosyVoice2 outputs 24 kHz = the server contract (`engines.py:38-39`); `resample_to_24k` becomes a passthrough (contrast VoxCPM's 48→24).

### DD-5: Non-verbals ride inline in tts_text; no engine logic

**Decision:** Pass `[laughter]`, `[breath]`, `<strong>…</strong>` through verbatim; the engine adds nothing.
**Rationale:** They are CosyVoice2 text tokens. The agent's emotion stripper only removes `[emotion…]` (`_ANY_TAG_RE`, `motion_events.py:36`), so these survive to `/tts` untouched. Whether the model *renders* them is **human gate T6**; authoring them (LLM/SOUL.md) is out of spike scope.

### DD-6: Streaming with a one-shot fallback

**Decision:** `for out in model.inference_*(..., stream=True): yield _emit_pcm(out["tts_speech"], self._sr)`, wrapped in `try/except TypeError` → non-stream call, mirroring `engines.py:343`.
**Rationale:** CosyVoice2 streaming yields dict chunks; API/version drift is real (VoxCPM had the same). The adapter isolates it.

### DD-7: Third isolated venv `.venv-cosyvoice`

**Decision:** CosyVoice2 (funasr / matcha-tts / WeTextProcessing / pynini) gets its own venv; Qwen `.venv` and VoxCPM `.venv-voxcpm` untouched; NSSM `Application` selects.
**Rationale:** Dependency conflicts already forced a 2nd venv for VoxCPM (`[[desktop-tts-voxcpm2-prod]]`); CosyVoice's text-frontend stack conflicts further.

### DD-8: THE technical gate measures RTF through the WS service with A2F concurrent (T9)

**Decision:** Stand up a *draft* CosyVoiceEngine in `.venv-cosyvoice`, run `server.py` on an alt port (e.g. 8012) with `A2F_FORK_ENABLED=1` → real A2F (`:8003`), warm it, then measure warm streaming RTF + time-to-first-audio.
**Rationale:** Exactly the miss that killed VoxCPM — standalone RTF hid the real prod-with-A2F RTF ≈ 1.65 (`docs/research/2026-07-09-voxcpm2-tts-spike.md:106-113`; `[[desktop-tts-voxcpm2-prod]]`). A2F adds ~1.6–1.7× GPU contention.
**Alternative:** Standalone RTF — explicitly rejected as the known-bad methodology.

### DD-9: Install target — Windows-native first, fall back to WSL2

**Decision:** T1 attempts Windows-native install; if `pynini`/`ttsfrd`/Matcha-TTS won't build, fall back to WSL2 (precedent: A2F runs in WSL2, `[[desktop-a2f-nim-wsl]]`). The chosen target reshapes T13 (NSSM `Application` flip if Windows; a WSL2 `ws://` service like A2F if WSL2).
**Rationale:** Discoverable only by attempting the install — resolved in-flight, not by asking.

### DD-10: Warmup generation in load()

**Decision:** One throwaway Russian gen at service start (mirrors `engines.py:299-310`).
**Rationale:** Cold-start latency must not hit the first real turn; the RTF gate measures **warm**.

## Tasks

### Task 1: Isolated venv + CosyVoice2 install (platform decision)

- **Location:** remote-desktop
- **Files:** Desktop `E:\cosyvoice-spike\.venv` (+ install log)
- **Depends on:** none
- **Scope:** L
- **What:** Create the isolated venv; install `FunAudioLLM/CosyVoice` with its deps, the Matcha-TTS submodule, and `pynini`/`ttsfrd`/WeTextProcessing; torch/torchaudio from the cu124 index; set `HF_HUB_DISABLE_XET=1`, `HF_HUB_CACHE=E:\AI\models\hub`. Attempt Windows-native; fall back to WSL2 (DD-9) and record which.
- **How:** Detach the long install via WMI (`Invoke-CimMethod Win32_Process Create`) writing to `E:\cosyvoice-spike\install.log` with DONE/FAIL markers; poll in <30 s reads. Put `$var`/nested-quote logic in an uploaded `.ps1`.
- **Context:** `[[desktop-ssh-long-running-procs]]`; `[[desktop-tts-torchaudio-cu124]]`; `[[desktop-hf-xet-unreachable]]`; `[[desktop-a2f-nim-wsl]]`.
- **Verify:** `python -c "import torch,cosyvoice; print(torch.cuda.is_available())"` → `True`; install.log ends DONE.
- **Gate:** install succeeds AND CUDA true AND target (Windows vs WSL2) recorded. If unbuildable on both → **no-go**, jump to Task 15, stop.

### Task 2: Download CosyVoice2-0.5B weights

- **Location:** remote-desktop
- **Files:** `E:\AI\models\hub\...FunAudioLLM--CosyVoice2-0.5B`
- **Depends on:** Task 1
- **Scope:** S
- **What:** Download `FunAudioLLM/CosyVoice2-0.5B` to the E: HF cache.
- **How:** Detached HF download with `HF_HUB_DISABLE_XET=1`; poll for the snapshot dir.
- **Context:** `[[desktop-hf-xet-unreachable]]`.
- **Verify:** snapshot dir populated; `du` shows expected size.

### Task 3: Generate Russian clone samples (pasha) + deliver to Pi

- **Location:** remote-desktop
- **Files:** `E:\cosyvoice-spike\samples\ru_clone_*.wav`; pulled to `/home/priney/cosyvoice-samples/`
- **Depends on:** Task 2
- **Scope:** M
- **What:** Minimal spike synth script: load CosyVoice2, `inference_zero_shot` with `prompt_speech_16k` = the pasha ref WAV + `prompt_text` = its transcript (paths from the live `.env` `TTS_REF_*`), synthesize 5–6 Russian sentences. Pull the WAVs to the Pi.
- **How:** Detached WMI job → log; `ssh_download` / rsync WAVs to the Pi.
- **Context:** ref path recipe in `[[desktop-tts-voxcpm2-prod]]`; zero-shot needs `prompt_text` (DD-2).
- **Verify:** WAVs exist on the Pi, non-empty, playable.

### Task 4: HUMAN GATE — Russian naturalness + timbre verdict (DECISIVE)

- **Location:** human-gate
- **Files:** — (verdict recorded into the spike log at Task 15)
- **Depends on:** Task 3
- **Scope:** S (human)
- **What:** Human listens to the pasha Russian samples and judges (a) Russian naturalness/intelligibility, (b) timbre match. **This is the decisive gate** — primary sources conflict on whether CosyVoice2-0.5B even supports Russian.
- **How:** Not automatable. Present the WAVs; capture green/red + notes.
- **Verify:** verdict recorded.
- **Gate:** **GREEN → proceed. RED → STOP; document no-go (Task 15 + Task 16), keep prod on Qwen, VoxCPM stays the documented fallback.** Tasks 5–14 and 16-integration are conditional on this.

### Task 5: Generate emotion + non-verbal samples + deliver to Pi

- **Location:** remote-desktop
- **Files:** `E:\cosyvoice-spike\samples\emo_*.wav`, `nv_*.wav`; pulled to Pi
- **Depends on:** Task 4 (green)
- **Scope:** M
- **What:** Synthesize (a) one Russian sentence across the emotion map via `inference_instruct2` + `<|endofprompt|>` (`грустно`, `радостно`, `удивлённо`, `задумчиво`, plus neutral A/B); (b) sentences containing `[laughter]`, `[breath]`, `<strong>…</strong>`. Pull to the Pi.
- **How:** Extend the Task 3 script with the instruct2 path (DD-3) and inline non-verbal tokens (DD-5); detached; download.
- **Context:** `VOXCPM_EMOTION_PROMPTS` pattern (`engines.py:266`); issue steps 4–5.
- **Verify:** emotion + non-verbal WAVs on the Pi.

### Task 6: HUMAN GATE — emotion + non-verbal verdict

- **Location:** human-gate
- **Files:** —
- **Depends on:** Task 5
- **Scope:** S (human)
- **What:** Human judges: emotions audible & correct in Russian; `[laughter]`/`[breath]`/`<strong>` render as intended.
- **Verify:** verdict recorded.
- **Gate:** GREEN → integration may proceed. If emotion/non-verbals fail but Russian (Task 4) passed → possible reduced-scope go (clone-only, no expressive) — flag for the human's call; note in spike log.

### Task 9: TECHNICAL GATE — warm streaming RTF + TTFA through the WS service WITH A2F concurrent

- **Location:** remote-desktop
- **Files:** draft `CosyVoiceEngine` in `.venv-cosyvoice` (throwaway), `E:\cosyvoice-spike\bench\*`; results → spike log
- **Depends on:** Task 4 (green)
- **Scope:** L
- **What:** Wire a **draft** CosyVoiceEngine into a copy of `server.py` on an alt port (8012) in `.venv-cosyvoice`, `A2F_FORK_ENABLED=1` → real A2F (`:8003`); warm it; measure warm streaming RTF and time-to-first-audio **while A2F is forking concurrently**. Record resident VRAM alongside STT+A2F.
- **How:** Reuse `server.py` unchanged (engine-agnostic); drive `/tts` over WS with Russian sentences; time first PCM byte and audio-vs-wall. Do NOT disturb prod TTS (`:8002`) beyond a brief controlled window; keep STT/A2F up.
- **Context:** the lesson — `docs/research/2026-07-09-voxcpm2-tts-spike.md:106-113`, `[[desktop-tts-voxcpm2-prod]]` (RTF 2.97 w/ A2F vs 0.67 Qwen); `infra/desktop/tts/a2f_fork.py:168`, `server.py:217`.
- **Verify:** warm streaming RTF and TTFA recorded, measured with A2F active.
- **Gate:** **warm RTF comfortably < ~0.8 with A2F concurrent AND low TTFA → proceed. RTF ≥ ~0.8 → no-go** (same failure as VoxCPM); document (Task 15/16), stay on Qwen.

### Task 10: Implement CosyVoiceEngine + registry + emotion map — CONDITIONAL (T4 ∧ T6 ∧ T9 green)

- **Location:** local-repo
- **Files:** `infra/desktop/tts/engines.py`
- **Depends on:** Task 9 (green); promotes the Task 9 draft
- **Scope:** M
- **What:** Add `CosyVoiceEngine` (DD-1,2,3,4,6,10): env config, `_parse_refs`/`_resolve` reuse, `load()`+warmup, `stream_pcm()` routing neutral→`inference_zero_shot`, emotion→`inference_instruct2`+`<|endofprompt|>`, `_emit_pcm(chunk, 24000)`, try/except one-shot fallback, `health_fields()`. Add `COSYVOICE_EMOTION_PROMPTS`. Register `"cosyvoice"` (`engines.py:362`) and add to `__all__` (`engines.py:384`).
- **How:** Copy `VoxCPMEngine` (`engines.py:275-353`); swap the model API + emotion mechanism; `self._sr=24000`.
- **Context:** `engines.py:116-169, 266-353, 362-393`.
- **Verify:** `from_env()` with `TTS_ENGINE=cosyvoice` constructs it; `python -c` import clean.

### Task 11: .env.example cosyvoice block — CONDITIONAL

- **Location:** local-repo
- **Files:** `infra/desktop/tts/.env.example`
- **Depends on:** Task 10
- **Scope:** S
- **What:** Add a documented `cosyvoice` engine block (model id, `TTS_REF_*`, emotion note) mirroring the voice_clone block (`.env.example:26-43`). Closes the same gap VoxCPM left.
- **Context:** `infra/desktop/tts/.env.example:14-56`.
- **Verify:** block present; comments consistent with `_parse_refs` var names.

### Task 12: Tests — _FakeCosyVoiceModel + dispatch/stream/voice/emotion — CONDITIONAL

- **Location:** local-repo
- **Files:** `infra/desktop/tts/tests/test_synthesize.py`
- **Depends on:** Task 10
- **Scope:** M
- **What:** Add `_FakeCosyVoiceModel` (records `inference_zero_shot`/`inference_instruct2` kwargs) + tests: selection via `TTS_ENGINE=cosyvoice`; neutral→zero_shot with `prompt_text`; emotion→instruct2 with the mapped Russian word + `<|endofprompt|>`; voice/default resolution; 24 kHz PCM16 output.
- **How:** Follow `test_synthesize.py:168-256` (voice_clone cases); add to `isolate_env` clear list (`test_synthesize.py:70`).
- **Context:** `test_synthesize.py:34-53, 70, 168-256`.
- **Verify:** `pytest infra/desktop/tts/tests/test_synthesize.py -q` — green.

### Task 13: Two-venv deploy + NSSM/WSL flip, Qwen fallback intact — CONDITIONAL

- **Location:** remote-desktop
- **Files:** Desktop `.venv-cosyvoice`, NSSM `voice-agent-tts` `Application`, prod `.env` (+ `.env.qwen-backup`)
- **Depends on:** Task 10, Task 11 (and Task 12 green)
- **Scope:** L
- **What:** Build `.venv-cosyvoice` in the repo checkout; `git pull` the engine on the Desktop; set `.env` (`TTS_ENGINE=cosyvoice`, model id, `TTS_REF_*`, `HF_HUB_CACHE`), save old as `.env.qwen-backup`; flip NSSM `Application` → `.venv-cosyvoice` python (or, if Task 1 chose WSL2, deploy as a WSL2 service like A2F). Keep Qwen `.venv` + `.env.qwen-backup` for instant rollback.
- **How:** `[[desktop-tts-voxcpm2-prod]]` deploy/rollback recipe; Desktop pull via Windows-side git (`git -C E:/voice-agent-repo pull --ff-only` — WSL pull fails per `[[desktop-ssh-long-running-procs]]`).
- **Context:** `deploy.sh:24`; `[[desktop-tts-voxcpm2-prod]]`; `[[voice-agent-deployment]]`.
- **Verify:** `GET :8002/health` → `engine: cosyvoice`, `model_loaded: true`.
- **Gate:** service healthy; rollback path (`.env.qwen-backup` + NSSM flip) documented & tested.

### Task 14: E2E prod validation — PCM + A2F blendshapes — CONDITIONAL

- **Location:** remote-desktop → human-gate
- **Files:** — (E2E evidence → spike log)
- **Depends on:** Task 13
- **Scope:** M
- **What:** Drive `/tts` WS with `{"text": <RU>, "voice":"default", "emotion":"happy"}`; confirm PCM16@24k stream + `{"done":true}` + A2F blendshape frames + `a2f_done`; check full-stack VRAM. Then a short human end-to-end listen through the live agent.
- **How:** WS client script (mirror the VoxCPM E2E: "emotion=happy → PCM + N blendshape frames + a2f_done", `[[desktop-tts-voxcpm2-prod]]`); `nvidia-smi` for resident set.
- **Context:** `infra/desktop/tts/server.py:217`, `a2f_fork.py:133/168`.
- **Verify:** PCM + blendshapes + `a2f_done` observed; VRAM within 12 GB; human confirms live turn sounds right.
- **Gate:** E2E green → keep; else roll back to Qwen.

### Task 15: Spike log — ALWAYS

- **Location:** local-repo
- **Files:** `docs/research/2026-07-10-cosyvoice2-tts-spike.md` (new)
- **Depends on:** Task 4, Task 6, Task 9 (records verdicts) + Task 14 if reached
- **Scope:** M
- **What:** Follow the VoxCPM spike-log format (Why / What CosyVoice2 gives / Constraints / Steps table / Go-no-go / Log). Record every gate verdict — including a no-go and its reason if the spike stops early. **Explicitly log the warm-RTF-with-A2F number** (the metric VoxCPM's log missed).
- **Context:** `docs/research/2026-07-09-voxcpm2-tts-spike.md`.
- **Verify:** file renders; gate outcomes captured.

### Task 16: ADR-0019 — CONDITIONAL (green) or a no-go ADR

- **Location:** local-repo
- **Files:** `docs/adr/0019-cosyvoice2-russian-expressive-tts.md` (new)
- **Depends on:** Task 14 (green) — or a "rejected/superseded" ADR if the spike went no-go
- **Scope:** M
- **What:** ADR in the 0018 format (frontmatter tags/status/relates-to; Decision; Alternatives; Consequences). Green: CosyVoice2 replaces Qwen as expressive engine. No-go: record the decision to stay on Qwen with reasons.
- **Context:** `docs/adr/0018-voxcpm2-expressive-tts.md`; `relates-to` 0018 (fallback) + 0016 (A2F emotion).
- **Verify:** ADR renders; number 0019; status matches outcome.

### Task 17: Validation

- **Location:** local-repo + remote-desktop
- **Files:** —
- **Depends on:** all
- **Scope:** S
- **What:** Final consistency pass. If green — `pytest` green, `/health` shows `cosyvoice`, E2E PCM+A2F confirmed, Qwen rollback intact, spike log + ADR committed. If no-go — prod untouched on Qwen, spike log + no-go ADR committed, no half-applied integration. Confirm `test_server.py`/`test_server_fork.py` still green (engine-agnostic).
- **Context:** —
- **Verify:** `pytest infra/desktop/tts/tests/ -q`; `curl :8002/health`; `git status` clean/committed.

## Execution

- **Mode:** sub-agents
- **Parallel:** false
- **Reasoning:** The pipeline is inherently serial and cannot run AFK — two decisive gates (Task 4 Russian, Task 6 emotion/non-verbals) are **human listening verdicts** and the technical gate (Task 9) needs a controlled single-tenant window on one 12 GB GPU shared with live STT/A2F, so a single driver synthesizes on the Desktop, pulls samples to the Pi, **stops for the human**, then proceeds; the nominal Task 5/6 ∥ Task 9 parallelism is illusory (both contend for the same GPU and the same human).
- **Order:**
  Task 1 → Task 2 → Task 3 → **[Task 4 HUMAN GATE — Russian, DECISIVE]**
  ─── barrier (green) ───
  Task 5 → **[Task 6 HUMAN GATE — emotion/non-verbals]** → Task 9 **[TECH GATE — RTF w/ A2F]**
  ─── barrier (T4 ∧ T6 ∧ T9 green) ───
  Task 10 → Task 11 → Task 12 → Task 13 → Task 14
  ─── barrier ───
  Task 15 → Task 16 → Task 17
  Any hard stop (Task 1 unbuildable · **Task 4 red** · Task 9 RTF ≥ ~0.8) → jump to Task 15 (no-go log) + Task 16 (no-go ADR), prod stays Qwen, Task 17.

## Verification

Acceptance criteria (from the issue):

- [ ] CosyVoice2-0.5B Russian quality judged acceptable (human listen) on the cloned `pasha` voice.
- [ ] Emotion instruction + `[laughter]`/`[breath]` non-verbals audibly work in Russian.
- [ ] Warm streaming **RTF < ~0.8 with A2F running concurrently**, low time-to-first-audio.
- [ ] If green: `CosyVoiceEngine` integrated (two-venv), Qwen fallback intact, deployed + E2E validated (PCM + A2F blendshapes).

## Materials

- CosyVoice2 repo: https://github.com/FunAudioLLM/CosyVoice · model: https://huggingface.co/FunAudioLLM/CosyVoice2-0.5B · paper arXiv 2412.10117 · demo https://funaudiollm.github.io/cosyvoice2/
- VoxCPM2 (fallback): https://huggingface.co/openbmb/VoxCPM2 · streaming https://deepwiki.com/OpenBMB/VoxCPM/7.2-streaming-generation
- In-repo: `docs/adr/0018-voxcpm2-expressive-tts.md`, `docs/research/2026-07-09-voxcpm2-tts-spike.md`, `infra/desktop/tts/engines.py`, `synthesize.py`, `server.py`
- VoxCPM samples on the Pi: `/home/priney/voxcpm-samples/` (v2-correct = the good ones)
