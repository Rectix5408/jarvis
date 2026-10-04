// SPDX-License-Identifier: Apache-2.0
const assert = require('node:assert/strict'); const fs = require('node:fs'); const vm = require('node:vm');
let checks = 0, pending = [], audios = [], revoked = [], states = [];
const check = (value, message) => { assert.ok(value, message); checks++; };
const context = { window: {}, AbortController, URL: { createObjectURL: () => `blob:${audios.length}`, revokeObjectURL: url => revoked.push(url) },
  fetch: (path, options) => new Promise(resolve => pending.push({ resolve, options })),
  Audio: class { constructor(url) { this.src = url; this.played = false; audios.push(this); } async play() { this.played = true; this.onplaying?.(); } pause() { this.paused = true; } },
};
vm.runInNewContext(fs.readFileSync('frontend/js/audio_output.js', 'utf8'), context);
const response = { ok: true, blob: async () => ({}) };
(async () => {
  const output = new context.window.JarvisAudioOutput(state => states.push(state));
  const first = output.speak('First', '', 'fixture'); output.stop(); pending.shift().resolve(response); await first;
  check(!audios.length, 'Stopped fetch cannot start late audio');
  const old = output.speak('Old', '', 'fixture'); const oldRequest = pending.shift();
  const latest = output.speak('Latest', '', 'fixture'); const latestRequest = pending.shift();
  check(oldRequest.options.signal.aborted, 'New response aborts previous synthesis');
  latestRequest.resolve(response); await latest; oldRequest.resolve(response); await old;
  check(audios.length === 1 && audios[0].played, 'Only newest synthesis plays');
  check(states.at(-1) === 'speaking', 'Actual playing updates activity');
  output.stop(); check(audios[0].paused && audios[0].src === '', 'Stop cancels playback');
  check(revoked.length === 1, 'Stop releases object URL');
  const failed = output.speak('Fail', '', 'fixture'); pending.shift().resolve({ ok: false }); await failed;
  check(states.at(-1) === 'error', 'Synthesis failure sets error');
  const before = pending.length; await output.speak('No credential', '', '');
  check(pending.length === before, 'No synthesis without authentication');
  console.log(`Audio output: ${checks} assertions passed`);
})().catch(error => { console.error(error); process.exitCode = 1; });
