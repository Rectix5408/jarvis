// SPDX-License-Identifier: Apache-2.0
(() => {
  'use strict';
  // Recognition is suspended during playback: speaker echo must never become an instruction.
  class Conversation {
    constructor({ createRecognition, send, cancel, stopAudio, speak, onState }) {
      Object.assign(this, { createRecognition, send, cancel, stopAudio, speak, onState });
      this.enabled = false; this.running = false; this.pending = null;
      this.recognition = null; this.generation = 0; this.silences = 0; this.state = 'idle';
    }
    transition(state) { this.state = state; this.onState(state, this.enabled); }
    releaseMic() {
      const recognition = this.recognition; this.recognition = null;
      if (recognition) {
        recognition.onstart = recognition.onresult = recognition.onerror = recognition.onend = null;
        try { recognition.abort(); } catch (_) { /* Already ended. */ }
      }
    }
    start() {
      if (this.enabled) return;
      this.enabled = true; this.silences = 0; this.interrupt();
    }
    stop(state = 'idle') {
      this.enabled = false; this.generation++; this.pending = null;
      this.releaseMic(); this.stopAudio(); this.transition(state);
    }
    interrupt() {
      if (!this.enabled) return;
      this.generation++; this.stopAudio(); this.releaseMic();
      if (this.running) { this.discardResponse = true; this.cancel(); }
      this.listen();
    }
    listen() {
      if (!this.enabled || this.recognition) return;
      const generation = this.generation;
      let recognition;
      try { recognition = this.createRecognition(); } catch (_) { this.stop('error'); return; }
      this.recognition = recognition;
      recognition.continuous = false; recognition.interimResults = false;
      let result = false;
      const current = () => this.enabled && generation === this.generation && this.recognition === recognition;
      recognition.onstart = () => { if (current()) this.transition('listening'); };
      recognition.onresult = event => {
        if (!current()) return;
        const text = Array.from(event.results).filter(item => item.isFinal !== false)
          .map(item => item[0].transcript).join(' ').trim();
        if (!text) return;
        result = true; this.silences = 0; this.releaseMic(); this.transition('understanding');
        if (/^(stopp?|stoppe|abbrechen)[.!?]?$/i.test(text)) {
          if (this.running) this.cancel();
          this.pending = null;
          if (this.running) this.transition('waiting'); else this.listen();
          return;
        }
        // Wait for the previous run's actual finished event before submitting another task.
        if (this.running) { this.pending = text; this.transition('waiting'); }
        else this.submit(text);
      };
      recognition.onerror = event => {
        if (!current()) return;
        if (event.error !== 'no-speech') this.stop('error');
      };
      recognition.onend = () => {
        if (!current()) return;
        this.recognition = null;
        if (!result && ++this.silences >= 3) this.stop();
        else if (!result) this.listen();
      };
      try { recognition.start(); } catch (_) { this.stop('error'); }
    }
    async submit(text) {
      const generation = this.generation;
      this.running = true; this.transition('thinking');
      try {
        const sent = await this.send(text);
        if (sent === false && generation === this.generation) { this.running = false; this.stop('error'); }
      } catch (_) { if (generation === this.generation) { this.running = false; this.stop('error'); } }
    }
    started() { this.running = true; if (this.enabled && !this.recognition) this.transition('thinking'); }
    finished(text) {
      this.running = false;
      if (this.discardResponse) { text = ''; this.discardResponse = false; }
      if (!this.enabled) return;
      if (this.pending) { const next = this.pending; this.pending = null; this.submit(next); return; }
      if (this.recognition) return;
      if (!text) { this.listen(); return; }
      const generation = this.generation;
      this.transition('waiting');
      this.speak(text, reason => {
        if (!this.enabled || generation !== this.generation) return;
        if (reason === 'ended') this.listen();
        else if (reason === 'error') this.stop('error');
      });
    }
    audioState(state) { if (this.enabled && state === 'speaking') this.transition('speaking'); }
  }
  window.JarvisConversation = Conversation;
})();
