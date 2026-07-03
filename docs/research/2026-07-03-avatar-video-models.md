# Research: Real-time Avatar & Video Models for Voice Agent

> **Date:** 2026-07-03
> **Context:** Exploring adding visual avatar (video or 3D) to the voice-agent project. Current stack: Pi 5 (LiveKit SFU + Agent Worker) + Desktop RTX 4070 (STT/TTS). Goal: self-hosted, real-time, integrated with LiveKit.

---

## 1. Problem Space

The current voice-agent has audio-only output. Adding a visual avatar requires generating real-time video or 3D animation synchronized with the agent's speech. Two fundamentally different approaches exist:

| Approach | Output | Latency | VRAM | Quality |
|----------|--------|---------|------|---------|
| **2D video generation** (talking head) | Rendered video frames | Medium (model inference) | High (4-6 GB) | Photorealistic |
| **3D blendshape animation** (Audio2Face) | ARKit blendshape weights | Very low (453 FPS) | Low (0.6 GB) | High (3D mesh) |
| **Full body motion** (EMAGE) | SMPL-X pose params | Low (~1.5x realtime) | Low (1-2 GB) | Full body |

**Key insight:** 3D blendshape/motion approaches generate *numbers* (not video), which means:
- Minimal bandwidth over network (~18 KB/s vs 10+ Mbps for video)
- Rendering happens client-side in browser (Three.js/WebGL)
- No GPU inference needed in browser — only 3D rendering
- Easy to integrate with LiveKit DataChannels

---

## 2. LiveKit Avatar Architecture

LiveKit supports virtual avatars via a **secondary participant** pattern:

```
LiveKit Room
├── Agent Worker (logic agent)
│     └── Audio output → DataStream → Avatar Worker
└── Avatar Worker (secondary participant)
      ├── Receives audio from agent
      ├── Generates video/animation frames
      └── Publishes video+audio track back to room
```

**Key API elements:**
- `AvatarSession` / `AvatarRunner` — Python helpers for custom avatar workers
- `DataStreamAudioOutput` — routes agent audio to avatar worker
- `lk.publish_on_behalf` attribute — links avatar tracks to agent
- `wait_playback_start` — synchronizes audio/video start
- Frontend: `useVoiceAssistant()` hook auto-handles avatar tracks

**Supported cloud providers (13):** Tavus, Anam, Runway, LemonSlice, bitHuman, Simli, D-ID, Beyond Presence, Avatario, AvatarTalk, LiveAvatar, Keyframe, TruGen

**None are self-hosted** — all require cloud API keys.

---

## 3. 2D Talking Head Models (Audio → Video)

### 3.1 SoulX-FlashHead (SOTA, 2026)

| Property | Value |
|----------|-------|
| **Source** | [Soul-AILab/SoulX-FlashHead](https://github.com/Soul-AILab/SoulX-FlashHead) |
| **Parameters** | 1.3B |
| **Performance** | 96 FPS on RTX 4090 |
| **VRAM** | ~4-6 GB |
| **Streaming** | Yes — infinite length, no identity drift |
| **Input** | Audio + reference portrait image |
| **Output** | Video frames (talking head) |
| **Training data** | 782 hours VividHead dataset |
| **License** | Open source |
| **Demo** | [YouTube](https://www.youtube.com/watch?v=0GR0B99zTl8) |

**Verdict:** Best performance/quality ratio. Runs on T4 (Colab). Streaming architecture matches voice-agent use case.

### 3.2 MuseTalk (Tencent)

| Property | Value |
|----------|-------|
| **Source** | [TMElyralab/MuseTalk](https://github.com/TMElyralab/MuseTalk) |
| **Performance** | 30+ FPS on V100 |
| **VRAM** | ~4-6 GB |
| **Type** | Latent space inpainting (not diffusion) |
| **Input** | Video + audio (modifies mouth region only) |
| **Output** | Video frames with lip-sync |
| **Version** | v1.5 available |
| **Demo** | [YouTube](https://www.youtube.com/watch?v=xVmyfYuZJS4) |

**Verdict:** Most popular, well-tested community model. Slower than FlashHead but proven.

### 3.3 SadTalker (CVPR 2023)

| Property | Value |
|----------|-------|
| **Source** | [OpenTalker/SadTalker](https://github.com/OpenTalker/SadTalker) |
| **Type** | 3DMM-based (audio → 3D motion coefficients → render) |
| **VRAM** | ~6.8 GB |
| **Performance** | Not real-time (several FPS) |
| **Features** | Lip sync, eye blinking, head poses, pose style control |
| **License** | Apache 2.0 |
| **Demo** | [Project page](https://sadtalker.github.io) |

**Verdict:** Outdated. Not suitable for real-time voice agent.

### 3.4 EchoMimic V2 (AntGroup, CVPR 2025)

| Property | Value |
|----------|-------|
| **Source** | [antgroup/echomimic_v2](https://github.com/antgroup/echomimic_v2) |
| **Type** | Audio-driven semi-body (upper body + face) |
| **Features** | Body gestures, hand movements, facial expressions |
| **Performance** | Diffusion-based, not real-time |
| **Demo** | [YouTube](https://www.youtube.com/watch?v=2ab6U1-nVTQ) |

**Verdict:** Good for upper-body animation but too slow for real-time.

### 3.5 ChatAnyone (Alibaba, 2025)

| Property | Value |
|----------|-------|
| **Source** | [HumanAIGC/chat-anyone](https://github.com/HumanAIGC/chat-anyone) |
| **Type** | Stylized real-time portrait video |
| **Performance** | 30 FPS on 4090, ~20-25 FPS on 4070 |
| **VRAM** | ~6-8 GB |
| **Features** | Upper body + hands, style control, hierarchical motion diffusion |
| **Resolution** | 512×768 max |
| **Demo** | [YouTube](https://www.youtube.com/watch?v=DeQpvjOT0v4) |

**Verdict:** Only model with style control + upper body. Heavy VRAM. Code may not be fully public.

---

## 4. 3D Animation Models (Audio → Motion Data)

### 4.1 NVIDIA Audio2Face-3D (Open Source)

| Property | Value |
|----------|-------|
| **Source** | [NVIDIA/Audio2Face-3D](https://github.com/NVIDIA/Audio2Face-3D) |
| **Output** | 52 ARKit blendshape weights (0.0-1.0) |
| **Models** | v2.3 (regression, 453 FPS, 0.6 GB) / v3.0 (diffusion, 3269 FPS, 4 GB) |
| **VRAM** | **0.6 GB** (v2.3) — negligible |
| **Protocol** | gRPC bidirectional streaming |
| **License** | NVIDIA Open Model License |
| **Components** | SDK, training framework, Maya/UE5 plugins, NIM microservice |
| **Features** | Skin, tongue, jaw, eyeballs; multi-identity; emotion labels |
| **Demo** | [YouTube (UE5)](https://www.youtube.com/watch?v=-l8kjQUTfQk) |

**How it works:**
1. Audio (PCM 16kHz) → neural network → 52 ARKit blendshape floats
2. Blendshapes → any 3D character (MetaHuman, VRM, Ready Player Me, custom mesh)
3. 3D engine (Unreal, Unity, Three.js) renders the mesh with applied blendshapes

**Key advantage for voice-agent:** Generates numbers, not video. ~6 KB/s at 30 FPS. Can stream via WebSocket/DataChannel to browser for client-side rendering.

**Web integration challenge:** Audio2Face uses gRPC (not supported in browsers). Solution: Python proxy (FastAPI WebSocket) between browser and gRPC endpoint.

### 4.2 EMAGE / PantoMatrix (CVPR 2024)

| Property | Value |
|----------|-------|
| **Source** | [PantoMatrix/PantoMatrix](https://github.com/PantoMatrix/PantoMatrix) |
| **Output** | SMPL-X body params + FLAME face params |
| **Coverage** | Full body: torso, arms, hands, head, face |
| **Performance** | ~0.615 sec per 1 sec video (~1.5x realtime) |
| **VRAM** | ~1-2 GB |
| **Dataset** | BEAT2: 76 hours, 30 speakers, 8 emotions, 4 languages |
| **Demo** | [Project page](https://pantomatrix.github.io/EMAGE) |

**Verdict:** Best open-source full-body gesture model. Combined with Audio2Face for face (higher quality), EMAGE for body/gestures.

### 4.3 NVIDIA Audio2Gesture

| Property | Value |
|----------|-------|
| **Source** | Part of Omniverse Machinima (2022.2+) |
| **Output** | Body gesture animation |
| **Status** | Not separately open source — only through Omniverse |
| **Integration** | Complements Audio2Face in Omniverse pipeline |

**Verdict:** Not usable standalone. Only through Omniverse ecosystem.

### 4.4 SyncAnimation (IJCAI 2025)

| Property | Value |
|----------|-------|
| **Type** | Audio → head pose + facial expression + upper body |
| **Modules** | AudioPose Syncer, AudioEmotion Syncer |
| **Status** | Research, code may not be public |

---

## 5. Browser-Side Rendering Options

### 5.1 Three.js + VRM

- `@pixiv/three-vrm` — load and animate VRM 3D avatars
- Apply blendshapes as morph targets
- 60 FPS rendering, no GPU inference in browser
- [Tutorial](https://www.youtube.com/watch?v=egQFAeu6Ihw) (59K views)

### 5.2 Ready Player Me + Three.js

- Cross-platform avatar format (GLTF/GLB)
- ARKit blendshape support
- [Agora ConvoAI tutorial](https://prod.agora.io/en/blog/build-real-time-ai-avatars-with-lip-sync-using-agora-convoai-rpm) — WebAudio API → viseme mapping

### 5.3 Gabber SDK

- Viseme stream from TTS → Three.js VRM
- [Tutorial](https://gabber.dev/blog/build-a-threejs-3d-avatar-with-realtime-ai-vision-voice-lip-sync-nextjs)
- [YouTube demo](https://www.youtube.com/watch?v=FLhrvHFCHiw)

### 5.4 Convai NeuroSync

- Cloud SDK for browser-based avatars
- Real-time lip sync, sub-200ms
- [Tutorial](https://convai.com/blog/ai-avatars-inside-browser-threejs-react-convai-web-sdk-tutorial)
- **Not self-hosted**

---

## 6. Proposed Architecture for Voice-Agent

### Option A: 3D Blendshape Pipeline (Recommended)

```
Desktop GPU (RTX 4070)
├── STT (faster-whisper, 2.5 GB)
├── TTS (Qwen3-TTS, 4 GB)
├── Audio2Face-3D v2.3 (0.6 GB) → 52 ARKit blendshapes
└── EMAGE (1-2 GB) → SMPL-X body pose params
      │
      ▼ WebSocket / DataChannel
      │
Pi 5 → LiveKit Room → Browser
      │
      ▼
Browser (Three.js)
├── VRM / Ready Player Me 3D model
├── Blendshapes → morph targets (face)
├── SMPL-X pose → skeletal animation (body/hands)
└── 60 FPS rendering
```

**VRAM budget (4070 12GB):**
| Component | VRAM |
|-----------|------|
| STT (whisper turbo) | ~2.5 GB |
| TTS (Qwen3-TTS) | ~4.0 GB |
| Audio2Face v2.3 | ~0.6 GB |
| EMAGE | ~1-2 GB |
| Windows + apps | ~1-2 GB |
| **Total** | **~9-11 GB** ✅ |

**Network bandwidth:**
- 52 blendshape floats × 30 FPS = ~6 KB/s
- SMPL-X pose (~100 params) × 30 FPS = ~12 KB/s
- **Total: ~18 KB/s** — negligible

### Option B: 2D Video Pipeline (Simpler, Heavier)

```
Desktop GPU (RTX 4070)
├── STT (faster-whisper, 2.5 GB)
├── TTS (Qwen3-TTS, 4 GB)
└── SoulX-FlashHead / MuseTalk (4-6 GB)
      │
      ▼ Video frames → LiveKit Video Track
      │
Browser plays video track
```

**VRAM budget:**
| Component | VRAM |
|-----------|------|
| STT (whisper turbo) | ~2.5 GB |
| TTS (Qwen3-TTS) | ~4.0 GB |
| FlashHead/MuseTalk | ~4-6 GB |
| Windows + apps | ~1-2 GB |
| **Total** | **~11.5-14.5 GB** ❌ (over 12GB) |

**Requires unload/reload pattern** — unload STT or TTS when avatar is speaking.

### Option C: ChatAnyone (All-in-one)

- Single model for face + body + hands + style
- 30 FPS on 4090, ~20-25 FPS on 4070
- VRAM ~6-8 GB
- Code may not be fully public yet

---

## 7. Implementation Path

### Phase 1: Audio2Face-3D Proxy (Minimal Viable Avatar)

1. Deploy Audio2Face-3D NIM on Desktop (Docker, gRPC)
2. Write Python FastAPI WebSocket proxy: browser audio → gRPC → blendshapes → WebSocket
3. Create Three.js test page with VRM avatar + blendshape morph targets
4. Integrate with LiveKit: avatar worker receives agent audio, publishes blendshapes via DataChannel

**Estimated effort:** 8-12 чч

### Phase 2: Add Body Motion (EMAGE)

1. Deploy EMAGE inference alongside Audio2Face
2. Merge SMPL-X pose with blendshape stream
3. Update Three.js renderer for skeletal animation

**Estimated effort:** 6-10 чч

### Phase 3: Full LiveKit Avatar Worker

1. Implement custom `AvatarRunner` following LiveKit's pattern
2. Wire `DataStreamAudioOutput` from agent to avatar worker
3. Handle `lk.publish_on_behalf` for frontend auto-detection
4. Add barge-in / interruption handling

**Estimated effort:** 4-8 чч

---

## 8. Key References

### Papers
- [SoulX-FlashHead (2026)](https://arxiv.org/abs/2602.07449) — 96 FPS streaming talking head
- [MuseTalk (2024)](https://arxiv.org/abs/2410.10122) — Real-time lip sync
- [EMAGE (CVPR 2024)](https://arxiv.org/abs/2401.00374) — Full body gesture from audio
- [Audio2Face-3D (2025)](https://arxiv.org/abs/2508.16401) — NVIDIA's facial animation
- [ChatAnyone (2025)](https://arxiv.org/abs/2503.21144) — Stylized portrait video
- [SyncAnimation (IJCAI 2025)](https://www.ijcai.org/proceedings/2025/0185.pdf) — Audio to pose + expression

### Code
- [SoulX-FlashHead](https://github.com/Soul-AILab/SoulX-FlashHead)
- [MuseTalk](https://github.com/TMElyralab/MuseTalk)
- [PantoMatrix / EMAGE](https://github.com/PantoMatrix/PantoMatrix)
- [Audio2Face-3D](https://github.com/NVIDIA/Audio2Face-3D)
- [Simli Avatar Runner (LiveKit reference)](https://github.com/dwain-barnes/simli-kokoro-whisper-livekit)
- [LiveKit Agents examples](https://github.com/livekit/agents/tree/main/examples/avatar_agents)

### LiveKit Docs
- [Virtual avatar models overview](https://docs.livekit.io/agents/models/avatar)
- [Images and video](https://docs.livekit.io/agents/multimodality/vision/video)
- [AvatarRunner API](https://docs.livekit.io/agents/models/avatar#custom-avatar-workers)

### YouTube Demos
- [SoulX-FlashHead on Colab](https://www.youtube.com/watch?v=0GR0B99zTl8)
- [MuseTalk lip-sync](https://www.youtube.com/watch?v=xVmyfYuZJS4)
- [Audio2Face-3D in UE5](https://www.youtube.com/watch?v=-l8kjQUTfQk)
- [ChatAnyone demo](https://www.youtube.com/watch?v=DeQpvjOT0v4)
- [EchoMimic V2 tutorial](https://www.youtube.com/watch?v=2ab6U1-nVTQ)
- [Three.js lip sync tutorial](https://www.youtube.com/watch?v=egQFAeu6Ihw)
- [Gabber VRM avatar](https://www.youtube.com/watch?v=FLhrvHFCHiw)

---

## 9. ARKit Blendshapes → Live2D Parameter Mapping

Audio2Face-3D outputs 52 ARKit blendshape weights (0.0–1.0). These map directly to Live2D Cubism standard parameters. This enables phoneme-level lip sync, eye blinking, eye tracking, brow expressions, and cheek movements on existing Live2D models — **without changing the model**.

### 9.1 Mouth (Lip Sync)

| ARKit Blendshape (0-1) | Live2D Parameter | Range | Formula |
|---|---|---|---|
| `jawOpen` | `ParamMouthOpenY` | 0→1 | direct value |
| `mouthSmileLeft` + `mouthSmileRight` | `ParamMouthForm` | -1→1 | `(left + right) / 2` |
| `mouthFrownLeft` + `mouthFrownRight` | `ParamMouthForm` | -1→1 | `-(left + right) / 2` |
| `mouthPucker` | `ParamMouthForm` | -1→1 | `-value` |
| `mouthFunnel` | `ParamMouthSize` | -1→1 | `-value` |
| `mouthLowerDownLeft` + `Right` | `ParamLipUnder` | 0→1 | `(left + right) / 2` |
| `mouthUpperUpLeft` + `Right` | `ParamLipUpper` | 0→1 | `(left + right) / 2` |
| `mouthShrugLower` | `ParamMouthOpenY` | — | subtle offset |

### 9.2 Eyes

| ARKit Blendshape | Live2D Parameter | Formula |
|---|---|---|
| `eyeBlinkLeft` | `ParamEyeLOpen` | `1 - blinkLeft` |
| `eyeBlinkRight` | `ParamEyeROpen` | `1 - blinkRight` |
| `eyeSquintLeft` | `ParamEyeLSmile` | direct value |
| `eyeSquintRight` | `ParamEyeRSmile` | direct value |
| `eyeWideLeft` | `ParamEyeLOpen` | `1 + wideLeft * 0.1` |
| `eyeWideRight` | `ParamEyeROpen` | `1 + wideRight * 0.1` |
| `eyeLookInLeft` / `eyeLookOutLeft` | `ParamEyeBallX` | `lookOut - lookIn` (left eye) |
| `eyeLookInRight` / `eyeLookOutRight` | `ParamEyeBallX` | `lookIn - lookOut` (right eye) |
| `eyeLookUpLeft` + `Right` | `ParamEyeBallY` | `(upL + upR) / 2` |
| `eyeLookDownLeft` + `Right` | `ParamEyeBallY` | `-(downL + downR) / 2` |

### 9.3 Brows

| ARKit Blendshape | Live2D Parameter | Formula |
|---|---|---|
| `browInnerUp` | `ParamBrowLY` + `ParamBrowRY` | `value` (both up) |
| `browDownLeft` | `ParamBrowLY` | `-value` |
| `browDownRight` | `ParamBrowRY` | `-value` |
| `browOuterUpLeft` | `ParamBrowLY` | `value` |
| `browOuterUpRight` | `ParamBrowRY` | `value` |
| `browDownLeft` + `browDownRight` | `ParamBrowLAngle` / `ParamBrowRAngle` | `-value` (anger) |

### 9.4 Cheeks / Nose

| ARKit Blendshape | Live2D Parameter | Formula |
|---|---|---|
| `cheekPuff` | `ParamPuffCheeks` (extended) | direct value |
| `cheekSquintLeft` + `Right` | `ParamCheek` | `(left + right) / 2` |
| `noseSneerLeft` + `Right` | — | no standard Live2D parameter |

### 9.5 Jaw / Head / Tongue

| ARKit Blendshape | Live2D Parameter | Formula |
|---|---|---|
| `jawLeft` / `jawRight` | `ParamMouthX` (extended) | `jawRight - jawLeft` |
| `jawForward` | — | no direct Live2D equivalent |
| `tongueOut` | `ParamTongue` (extended) | direct value |

### 9.6 Reference: Live2D Standard Parameters

Source: [Live2D Cubism Editor Manual — Standard Parameter List](https://docs.live2d.com/en/cubism-editor-manual/standard-parameter-list)

Key parameters for facial animation:
- `ParamMouthOpenY` (0=closed, 1=open) — mouth opening
- `ParamMouthForm` (-1=anger/frown, 0=neutral, 1=smile) — mouth shape
- `ParamEyeLOpen` / `ParamEyeROpen` (0=closed, 1=open) — eye blink
- `ParamEyeLSmile` / `ParamEyeRSmile` (0=neutral, 1=smiling) — eye squint
- `ParamEyeBallX` (-1=left, 0=center, 1=right) — eye gaze horizontal
- `ParamEyeBallY` (-1=down, 0=center, 1=up) — eye gaze vertical
- `ParamBrowLY` / `ParamBrowRY` (-1=down, 0=neutral, 1=up) — brow raise
- `ParamBrowLAngle` / `ParamBrowRAngle` (-1=anger, 0=neutral, 1=happy) — brow angle
- `ParamCheek` (0=none, 1=blush) — cheek color
- `ParamAngleX/Y/Z` — head rotation (not from Audio2Face)

Extended parameters (optional, model-dependent):
- `ParamPuffCheeks` — cheek puff
- `ParamTongue` — tongue movement
- `ParamLipUnder` / `ParamLipUpper` — lip vertical
- `ParamMouthX` — jaw horizontal
- `ParamMouthSize` — mouth funnel/pucker

### 9.7 Architecture: Audio2Face → Live2D Integration

```
Desktop GPU (RTX 4070)
├── TTS Service → audio PCM 24kHz
│      │
│      ├── (existing) audio → LiveKit room (WebRTC)
│      │
│      └── (new) audio → Audio2Face-3D NIM (gRPC)
│             │
│             ▼ 52 ARKit blendshape floats @ 30 FPS
│             │
└─────────────┘
              │
              ▼ WebSocket / LiveKit DataChannel
              │ (~6 KB/s)
              │
Browser (Cubism SDK Web)
├── arkitToLive2D(blendshapes)  ← ~50 lines JS mapping function
├── model.setParameterValueById("ParamMouthOpenY", jawOpen)
├── model.setParameterValueById("ParamMouthForm", smile - frown)
├── model.setParameterValueById("ParamEyeLOpen", 1 - eyeBlinkLeft)
├── model.setParameterValueById("ParamEyeROpen", 1 - eyeBlinkRight)
├── model.setParameterValueById("ParamEyeLSmile", eyeSquintLeft)
├── model.setParameterValueById("ParamEyeRSmile", eyeSquintRight)
├── model.setParameterValueById("ParamBrowLY", browInnerUp - browDownLeft)
├── model.setParameterValueById("ParamBrowRY", browInnerUp - browDownRight)
├── model.setParameterValueById("ParamEyeBallX", ...)
├── model.setParameterValueById("ParamEyeBallY", ...)
└── model.update()  ← 60 FPS render
```

### 9.8 Advantages Over Current Approach

Current Live2D animation uses:
- Volume-based lip sync (WebAudio AnalyserNode → `ParamMouthOpenY`)
- LLM emotion tags (neutral/happy/sad/surprised/thinking) → expression files
- Motion states (idle/listening/thinking/speaking) → motion files

Audio2Face replaces the first two with:
- **Phoneme-level lip sync** — accurate mouth shapes per sound, not just volume
- **Natural blinking** — eye blinks driven by audio prosody
- **Eye tracking** — gaze direction from speech patterns
- **Brow expressions** — emotional brow movement
- **Cheek/tongue** — extended expressivity (if model supports)

Motion states (idle/listening/thinking/speaking) remain unchanged — Audio2Face only enhances facial animation during speech.

### 9.9 Limitations

- **No head/body/hand motion** — Audio2Face is face-only. Head rotation (`ParamAngleX/Y/Z`), body (`ParamBodyAngleX/Y/Z`), and hands remain on current motion-state system or would need EMAGE integration.
- **gRPC not browser-native** — requires Python proxy (FastAPI WebSocket) between Audio2Face NIM and browser.
- **Model parameter availability** — extended parameters (`ParamPuffCheeks`, `ParamTongue`, etc.) depend on the Live2D model. Standard parameters (mouth, eyes, brows) are universally available.
- **Latency** — Audio2Face adds ~2-5ms inference (453 FPS) + network RTT. Total added latency: <50ms on LAN.
