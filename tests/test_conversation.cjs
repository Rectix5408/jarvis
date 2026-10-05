// SPDX-License-Identifier: Apache-2.0
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = { window: {} };
vm.runInNewContext(fs.readFileSync('frontend/js/conversation.js', 'utf8'), context);
let checks = 0;
function check(value, message) { assert.ok(value, message); checks++; }
function fixture(overrides = {}) {
  const inputs = [], sent = [], states = [], audio = [];
  let cancels = 0, stops = 0;
  const conversation = new context.window.JarvisConversation({
    createRecognition: () => {
      const input = { start() { this.onstart(); }, abort() { this.aborted = true; } };
      inputs.push(input); return input;
    },
    send: text => { sent.push(text); return true; },
    cancel: () => cancels++, stopAudio: () => stops++,
    speak: (text, done) => audio.push({ text, done }),
    onState: state => states.push(state), ...overrides,
  });
  const say = text => inputs.at(-1).onresult({ results: [[{ transcript: text }]] });
  return { conversation, inputs, sent, states, audio, say, cancels: () => cancels, stops: () => stops };
}
(async () => {
  const f = fixture(); f.conversation.start();
  check(f.conversation.state === 'listening', 'Explicit start opens recognition');
  f.say('Was steht heute an?');
  check(f.sent.length === 1 && f.inputs[0].aborted, 'Final utterance submits once and releases microphone');
  check(f.conversation.state === 'thinking', 'Inference starts thinking state');
  f.conversation.finished('Ein Termin.');
  check(f.audio.length === 1 && f.inputs.length === 1, 'Response speaks without listening to speaker echo');
  f.conversation.audioState('speaking');
  check(f.conversation.state === 'speaking', 'Actual playback controls speaking state');
  f.audio[0].done('ended');
  check(f.inputs.length === 2 && f.conversation.state === 'listening', 'Natural playback end resumes listening');
  f.say('Weitere Frage'); f.conversation.interrupt();
  check(f.cancels() === 1, 'Interrupt cancels actual active task');
  f.say('Nur heute');
  check(f.sent.length === 2 && f.conversation.state === 'waiting', 'New utterance waits for actual old run completion');
  f.conversation.finished('Alte Antwort');
  check(f.sent.at(-1) === 'Nur heute' && f.audio.length === 1, 'Old answer discarded; replacement sent after finish');
  f.conversation.finished('Neue Antwort'); const late = f.audio.at(-1).done;
  f.conversation.interrupt(); late('ended');
  check(f.inputs.length === 4, 'Late completion cannot create duplicate recognition');
  f.conversation.stop();
  check(!f.conversation.enabled && f.inputs.at(-1).aborted, 'Stop releases microphone and disables auto resume');
  late('ended'); check(f.inputs.length === 4, 'No restart after explicit stop');
  const denied = fixture(); denied.conversation.start();
  denied.inputs[0].onerror({ error: 'not-allowed' });
  check(!denied.conversation.enabled && denied.conversation.state === 'error', 'Permission failure disables conversation');
  const silent = fixture(); silent.conversation.start();
  for (let i = 0; i < 3; i++) silent.inputs.at(-1).onend();
  check(!silent.conversation.enabled && silent.inputs.length === 3, 'Repeated no-speech cannot restart forever');
  const broken = fixture({ send: async () => false }); broken.conversation.start(); broken.say('Hallo');
  await new Promise(setImmediate);
  check(broken.conversation.state === 'error' && !broken.conversation.running, 'Failed dispatch is not shown as running');
  const stop = fixture(); stop.conversation.start(); stop.say('Stopp.');
  check(stop.sent.length === 0 && stop.conversation.state === 'listening', 'Stop command is deterministic, without LLM request');
  const failure = fixture(); failure.conversation.start(); failure.say('Hallo'); failure.conversation.finished('Antwort');
  failure.audio[0].done('error');
  check(!failure.conversation.enabled, 'TTS failure stops auto conversation');
  console.log(`Conversation: ${checks} assertions passed`);
})().catch(error => { console.error(error); process.exitCode = 1; });
