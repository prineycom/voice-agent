// Transcript rendering. Uses EXACTLY ONE transcription source (see wire) so a
// segment never renders twice. Latency is reported via the injected onLatency
// hook instead of touching a DOM metric directly.
export function createTranscript(containerEl, { isLocal, onLatency, log }) {
  const lines = new Map();          // segment/stream id -> {el, textEl}
  let lastUserFinalAt = null;       // performance.now() when user finished speaking
  let emptyHintRemoved = false;     // query/remove the .empty hint at most once
  let localSeq = 0;                 // unique keys for locally-echoed typed messages

  // `mineOverride` forces the speaker side (used by addLocalMessage, since a
  // typed message has no participant identity to resolve via isLocal).
  function renderLine(key, identity, text, isFinal, mineOverride) {
    if (!emptyHintRemoved) {
      const hint = containerEl.querySelector('.empty');
      if (hint) hint.remove();
      emptyHintRemoved = true;
    }
    const mine = mineOverride !== undefined ? mineOverride : isLocal(identity);
    let line = lines.get(key);
    if (!line) {
      const el = document.createElement('div');
      el.className = `msg ${mine ? 'user' : 'agent'} partial`;
      const who = document.createElement('div');
      who.className = 'who';
      who.textContent = mine ? 'Вы' : `Агент (${identity || '?'})`;
      const textEl = document.createElement('div');
      el.append(who, textEl);
      containerEl.append(el);
      line = { el, textEl };
      lines.set(key, line);
    }
    line.textEl.textContent = text;
    if (isFinal) line.el.classList.remove('partial');
    containerEl.scrollTop = containerEl.scrollHeight;

    // Latency bookkeeping: stamp on user final, resolve on first agent text after it.
    const now = performance.now();
    if (mine && isFinal) {
      lastUserFinalAt = now;
    } else if (!mine && lastUserFinalAt != null) {
      onLatency(Math.round(now - lastUserFinalAt));
      lastUserFinalAt = null;
    }
  }

  // --- Transcriptions: use EXACTLY ONE source, or the same segment renders twice ---
  // (one finalized line + one stuck-partial dimmed line). Prefer TranscriptionReceived
  // — it carries the speaker participant and a clean `final` flag; only fall back to the
  // lk.transcription text stream when that event is unavailable.
  function wire(room) {
    const { RoomEvent } = window.LivekitClient;
    if (RoomEvent.TranscriptionReceived) {
      room.on(RoomEvent.TranscriptionReceived, (segments, participant) => {
        const identity = participant?.identity;
        // DIAGNOSTIC: log every transcription event so a repro shows whether the
        // events stop arriving (path issue) or keep arriving but fail to render.
        const who = isLocal(identity) ? 'you' : 'agent';
        const fin = (segments || []).some(s => s.final) ? ' FINAL' : '';
        log(`TR: ${who} x${(segments || []).length}${fin}`);
        try {
          for (const s of segments) renderLine(s.id, identity, s.text, s.final);
        } catch (e) {
          log('TR renderLine ERROR: ' + e.message);
        }
      });
      log('подписка на транскрипт: TranscriptionReceived');
      return;
    }
    if (typeof room.registerTextStreamHandler === 'function') {
      room.registerTextStreamHandler('lk.transcription', async (reader, info) => {
        const identity = info?.identity;
        const attrs = reader.info?.attributes || {};
        const key = reader.info?.id || attrs['lk.segment_id'] || `${identity}:${Date.now()}`;
        let text = '';
        for await (const chunk of reader) { text += chunk; renderLine(key, identity, text, false); }
        renderLine(key, identity, text, attrs['lk.transcription_final'] !== 'false');
      });
      log('подписка на транскрипт: text-stream lk.transcription');
    }
  }

  // Echo a message the user typed into the composer. The agent replies via the
  // 'lk.chat' text path but never sends the user's own text back as a
  // transcription, so we render it here as a final "Вы" line (which also stamps
  // the latency clock, so text turns get a voice-to-voice number too).
  function addLocalMessage(text) {
    renderLine(`local-${localSeq++}`, null, text, true, true);
  }

  function reset() {
    lines.clear();
    lastUserFinalAt = null;
    emptyHintRemoved = false;
  }

  return { wire, reset, addLocalMessage };
}
