// SPDX-License-Identifier: Apache-2.0; runs real app.js against a test-only DOM/API.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const { JSDOM } = require(process.env.JSDOM_PATH || '/tmp/jarvis-ui-test-tools/node_modules/jsdom');
const root = path.resolve(__dirname, '..');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function run() {
  const dom = new JSDOM(fs.readFileSync(path.join(root, 'frontend/settings.html'), 'utf8'), { runScripts: 'outside-only', url: 'https://localhost/settings' });
  const w = dom.window, calls = [];
  try {
    w.localStorage.setItem('jarvis_token', 'test-only-token');
    w.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
    w.requestAnimationFrame = cb => setTimeout(cb, 0); w.alert = () => {};
    w.confirm = () => false;
    let fail = false;
    w.fetch = async (url, options = {}) => {
      calls.push({ url: String(url), options });
      let body = {};
      if (url === '/api/me') body = { username: 'jarvis', is_admin: true, permissions: {} };
      if (url === '/api/verify-token') body = { valid: true, username: 'jarvis', is_admin: true };
      if (url === '/api/settings') body = { profiles: [], defaults: { google: { models: ['test'] }, anthropic: { models: ['claude-haiku-4-5-20251001'], url: 'https://api.anthropic.com/v1/messages' } } };
      if (url === '/api/profiles/test-response') body = fail ? { success: false, error: '<img src=x> denied' } : { success: true, message: 'Server OK', response: 'OK', latency_ms: 100, usage: { input_tokens: 8, output_tokens: 1 } };
      return { ok: true, status: 200, json: async () => body, text: async () => '', headers: { get: () => 'application/json' } };
    };
    for (const f of ['i18n.js', 'theme.js', 'app.js']) w.eval(fs.readFileSync(path.join(root, 'frontend/js', f), 'utf8'));
    await w._openSettingsModal(); await sleep(100);
    w.document.getElementById('btn-add-profile').click();
    const provider = w.document.getElementById('profile-provider');
    provider.value = 'anthropic'; provider.dispatchEvent(new w.Event('change'));
    w.document.getElementById('profile-model-input').value = 'claude-haiku-4-5-20251001';
    const button = w.document.getElementById('btn-test-profile-response');
    assert.equal(button.hidden, false);
    const previous = calls.length;
    button.click(); await sleep(20);
    assert.equal(calls.length, previous, 'Declined confirmation sends no billable request');
    w.confirm = () => true;
    w.document.getElementById('profile-api-key').value = 'test-key-not-real';
    button.click(); await sleep(30);
    const request = calls.find(c => c.url === '/api/profiles/test-response');
    assert.equal(request.options.headers.Authorization, 'Bearer test-only-token');
    assert.equal(JSON.parse(request.options.body).confirm_billable, true);
    assert.equal(JSON.parse(request.options.body).model, 'claude-haiku-4-5-20251001');
    const output = w.document.getElementById('profile-response-result');
    assert.match(output.textContent, /OK/); assert.equal(button.disabled, false);
    fail = true; button.click(); await sleep(20);
    assert.equal(output.querySelector('img'), null, 'Errors rendered as text, not HTML');
    assert.match(output.textContent, /denied/);
    w.document.getElementById('auth-session').checked = true;
    w.document.getElementById('auth-session').dispatchEvent(new w.Event('change'));
    assert.equal(button.hidden, true, 'No paid API test for browser session cookies');
    console.log('Profile response UI: confirmation, authenticated request, success/error and session visibility passed.');
  } finally { w.close(); }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
