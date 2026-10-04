// SPDX-License-Identifier: Apache-2.0
(() => {
  'use strict';
  class AudioOutput {
    constructor(onState = () => {}) {
      this.onState = onState; this.generation = 0; this.controller = null; this.audio = null; this.url = null;
    }
    stop() {
      this.generation++;
      this.controller?.abort(); this.controller = null;
      if (this.audio) { this.audio.onplaying = this.audio.onended = this.audio.onerror = null; this.audio.pause(); this.audio.src = ''; this.audio = null; }
      if (this.url) URL.revokeObjectURL(this.url); this.url = null;
      this.onState('idle');
    }
    async speak(text, voice, token) {
      this.stop(); const generation = this.generation;
      if (!text || !token) return;
      const controller = new AbortController(); this.controller = controller;
      try {
        const response = await fetch('/api/tts', {
          method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
          body: JSON.stringify({ text, voice }), signal: controller.signal,
        });
        if (!response.ok) throw new Error('Sprachausgabe nicht verfuegbar');
        const blob = await response.blob();
        if (generation !== this.generation || controller.signal.aborted) return;
        const url = URL.createObjectURL(blob), audio = new Audio(url); this.url = url; this.audio = audio;
        audio.onplaying = () => { if (generation === this.generation) this.onState('speaking'); };
        audio.onended = () => { if (generation === this.generation) this.stop(); };
        audio.onerror = () => { if (generation === this.generation) { this.stop(); this.onState('error'); } };
        await audio.play();
      } catch (error) {
        if (generation === this.generation && !controller.signal.aborted) { this.stop(); this.onState('error'); }
      } finally { if (this.controller === controller) this.controller = null; }
    }
  }
  window.JarvisAudioOutput = AudioOutput;
})();
