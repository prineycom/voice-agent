// LiveKit room controller. DOM-free: every UI side effect is delegated to
// hooks supplied by the shell. Reads the LiveKit SDK from the CDN UMD global
// (window.LivekitClient). Keeps `myIdentity` and `muted` as internal state.
export function createRoomController({ log, onConn, onError }) {
  let room = null;
  let myIdentity = null;
  // room.js is the single source of truth for `muted`; vu.js holds only a cache
  // written exclusively via vu.setMuted(...) calls from here.
  let muted = false;
  let hooks = null;   // the opts passed to connect(); reused by onDisconnected/toggleMute

  function isLocal(identity) { return identity && identity === myIdentity; }

  async function connect(opts) {
    hooks = opts;
    opts.onConnecting();
    onConn('подключение…', 'warn');
    try {
      const identity = 'web-' + Math.random().toString(36).slice(2, 7);
      const res = await fetch('/token?identity=' + encodeURIComponent(identity));
      if (!res.ok) throw new Error('token endpoint ' + res.status);
      const { token, url } = await res.json();
      myIdentity = identity;
      log(`токен получен, подключаюсь к ${url}`);

      const { Room, RoomEvent, Track } = window.LivekitClient;
      room = new Room({ adaptiveStream: true, dynacast: true });
      muted = false;
      opts.vu.setMuted(false);  // sync the vu cache to room.js's authoritative state
      opts.transcript.wire(room);
      opts.ops.wire(room);
      opts.ops.startTick();  // live-tick the running-task seconds

      room.on(RoomEvent.ConnectionStateChanged, (s) => log('состояние: ' + s));
      room.on(RoomEvent.Disconnected, () => onDisconnected());
      room.on(RoomEvent.TrackSubscribed, (track, pub, participant) => {
        if (track.kind === Track.Kind.Audio) {
          const el = track.attach();
          el.autoplay = true;
          opts.attachAudio(el);
          if (opts.lipsync) opts.lipsync.start(track.mediaStreamTrack);
          log('подписка на аудио агента (' + participant.identity + ')');
        }
      });
      room.on(RoomEvent.ParticipantConnected, (p) => { log('участник: ' + p.identity); opts.watchAgentParticipant(p); });
      room.on(RoomEvent.ParticipantAttributesChanged, (_changed, p) => {
        const st = p.attributes && p.attributes['lk.agent.state'];
        if (st) opts.onAgentState(st);
      });

      await room.connect(url, token);
      onConn('подключено', 'ok');
      log('в комнате: ' + room.name);

      // Existing remote participants (the agent may already be here).
      room.remoteParticipants.forEach(opts.watchAgentParticipant);

      await room.localParticipant.setMicrophoneEnabled(true);
      const micPub = room.localParticipant.getTrackPublication(Track.Source.Microphone);
      if (micPub?.track) {
        opts.onMicActive();
        opts.vu.start(micPub.track.mediaStreamTrack);
      }
      opts.setMuteEnabled(true);
      opts.onConnected();
    } catch (e) {
      log('ОШИБКА: ' + e.message);
      onConn('ошибка', 'err');
      opts.ops.stopTick();  // the 1s interval was started before the failure
      opts.ops.reset();
      room = null;          // discard the half-built room
      onError(e);
    }
  }

  function onDisconnected() {
    onConn('отключено', 'idle');
    muted = false;           // a fresh connection must start unmuted
    hooks.vu.setMuted(false);
    hooks.onAgentState(null);
    hooks.onMicInactive();   // mic label -> inactive, VU bar -> 0%
    hooks.vu.stop();
    if (hooks.lipsync) hooks.lipsync.stop();
    hooks.ops.stopTick();
    hooks.ops.reset();
    hooks.transcript.reset();
    hooks.setMuteEnabled(false);
    hooks.resetMuteUI();     // reset the mute button label to its default
    hooks.onDisconnectedUI();
  }

  async function disconnect() {
    log('отключаюсь');
    if (room) await room.disconnect();
    room = null;
  }

  async function toggleMute() {
    if (!room) return muted;
    muted = !muted;
    await room.localParticipant.setMicrophoneEnabled(!muted);
    if (hooks && hooks.vu) hooks.vu.setMuted(muted);
    return muted;
  }

  return {
    connect,
    disconnect,
    toggleMute,
    isLocal,
    get room() { return room; },
  };
}
