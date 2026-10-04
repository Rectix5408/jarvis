// SPDX-License-Identifier: Apache-2.0
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { createRequire } = require('node:module');
const { JSDOM } = createRequire(process.env.UI_NODE_MODULES ? `${process.env.UI_NODE_MODULES}/package.json` : '/tmp/jarvis-ui-test-tools/node_modules/package.json')('jsdom');
const source = fs.readFileSync('frontend/js/shell.js', 'utf8');
const tick = () => new Promise(resolve => setTimeout(resolve, 25));
let checks = 0;
function check(value, message) { assert.ok(value, message); checks++; }
async function fixture({ admin = false, auth = true, login = false } = {}) {
  const dom = new JSDOM(`<body>${login ? '<div id="login-screen">Login</div>' : ''}</body>`, { url: 'https://jarvis.test/chat', runScripts: 'outside-only' });
  const w = dom.window, requests = [];
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; };
  if (auth) w.localStorage.setItem('jarvis_token', 'fixture-only');
  let expired = false;
  w.fetch = async (path, options) => {
    requests.push({ path, options });
    return { ok: !expired, status: expired ? 401 : 200, json: async () => path === '/api/me' ? { username: '<img src=x onerror=alert(1)>', is_admin: admin } : path === '/api/cpu' ? { cpu: 17.5 } : { profile_name: 'Local test', status: 'ok' } };
  };
  w.eval(source); await tick();
  return { w, requests, expire: () => { expired = true; }, close: () => dom.window.close() };
}
(async () => {
  for (const page of ['dashboard', 'chat', 'wissen', 'settings', 'portal']) {
    const html = fs.readFileSync(`frontend/${page}.html`, 'utf8');
    check(html.includes('/static/css/shell.css') && html.includes('/static/js/shell.js'), `${page} uses shared shell`);
  }
  const guest = await fixture({ auth: false });
  check(guest.requests.length === 0, 'Guest does not call private APIs');
  check(guest.w.document.getElementById('jarvis-shell').hidden, 'Guest shell hidden'); guest.close();
  const regular = await fixture(); const d = regular.w.document;
  check(!d.getElementById('jarvis-shell').hidden, 'Authenticated shell visible');
  check(!d.querySelector('.js-nav a[href^="/settings"]'), 'Admin links excluded for normal users');
  check(d.querySelector('.js-footer').textContent.includes('<img'), 'Username rendered as text');
  check(!d.querySelector('.js-footer img'), 'Username cannot inject HTML');
  d.querySelector('[aria-label="Arbeitsbereich suchen"]').click();
  check(d.querySelector('.js-palette').open, 'Command palette opens');
  const search = d.querySelector('.js-search'); search.value = 'wissen'; search.dispatchEvent(new regular.w.Event('input'));
  check(d.querySelectorAll('.js-results a').length === 1, 'Command filtering works');
  d.querySelector('[aria-label="Systemstatus"]').click(); await tick();
  check(d.querySelector('.js-details').textContent.includes('17.5 %'), 'CPU uses real API value');
  check(regular.requests.every(r => r.options.headers.Authorization === 'Bearer fixture-only'), 'Private requests authenticated');
  regular.expire(); regular.w.dispatchEvent(new regular.w.Event('focus')); await tick();
  check(d.getElementById('jarvis-shell').hidden && !d.querySelector('.js-context').open, '401 removes shell and sensitive panel'); regular.close();
  const admin = await fixture({ admin: true });
  check(admin.w.document.querySelector('.js-nav a[href="/settings#cron"]'), 'Admin task link uses existing backend UI');
  admin.w.eval(source); check(admin.w.document.querySelectorAll('#jarvis-shell').length === 1, 'Mount is idempotent'); admin.close();
  const login = await fixture({ login: true });
  check(login.w.document.getElementById('jarvis-shell').hidden, 'Login screen not covered by shell');
  login.w.document.getElementById('login-screen').style.display = 'none'; await tick();
  check(!login.w.document.getElementById('jarvis-shell').hidden, 'Shell appears after login screen closes'); login.close();
  console.log(`Shell: ${checks} assertions passed`);
})().catch(error => { console.error(error); process.exitCode = 1; });
