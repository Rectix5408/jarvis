// SPDX-License-Identifier: Apache-2.0
const assert = require('assert');
const fs = require('fs');
const { JSDOM } = require('/tmp/jarvis-ui-test-tools/node_modules/jsdom');

const html = fs.readFileSync('frontend/agents.html', 'utf8');
const script = fs.readFileSync('frontend/js/agents.js', 'utf8');
const baseAgent = { id:'a1', owner:'alice', name:'Research', description:'Local research', goal:'Pruefen', instructions:'', profile_id:'', tools:['read_file'], knowledge:[], autonomy:'supervised', max_steps:4, timeout_seconds:30, token_budget:500, cost_budget:0, tool_call_limit:2, permissions:{read:true}, enabled:true };
let agents = [baseAgent], requests = [];
const response = value => Promise.resolve({ ok:true, json:async()=>value });
const fetch = async (url, options={}) => {
  requests.push({url, options});
  if (url.endsWith('/metadata')) return response({tools:['read_file','write_file'], profiles:[]});
  if (url.includes('/approvals?')) return response([]);
  if (url.endsWith('/runs')) return response([]);
  if (url.endsWith('/agents') && options.method === 'POST') { agents = [{...baseAgent, ...JSON.parse(options.body), id:'a2'}]; return response(agents[0]); }
  if (url.endsWith('/agents')) return response(agents);
  if (url.includes('/agents/') && options.method === 'PUT') { agents = [{...agents[0], ...JSON.parse(options.body)}]; return response(agents[0]); }
  return response({});
};

(async () => {
  const dom = new JSDOM(html, {url:'http://localhost/agents', runScripts:'outside-only'});
  const {window} = dom; window.fetch = fetch; window.AbortController = global.AbortController;
  Object.defineProperty(window.document, 'hidden', {value:false, configurable:true});
  window.localStorage.setItem('jarvis_token','secret');
  window.HTMLDialogElement.prototype.showModal = function(){ this.open = true; };
  window.HTMLDialogElement.prototype.close = function(){ this.open = false; };
  window.confirm = () => true; window.lucide = {createIcons(){}};
  window.eval(script);
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.strictEqual(window.document.querySelectorAll('.agent-card').length, 1);
  assert.strictEqual(window.document.getElementById('agent-count').textContent, '1');

  window.document.getElementById('new-agent').click();
  window.document.getElementById('agent-name').value = 'Writer';
  window.document.getElementById('agent-goal').value = 'Write safely';
  window.document.getElementById('agent-tool-limit').value = '3';
  window.document.getElementById('agent-form').dispatchEvent(new window.Event('submit', {bubbles:true, cancelable:true}));
  await new Promise(resolve => setTimeout(resolve, 30));
  const create = requests.find(item => item.url.endsWith('/agents') && item.options.method === 'POST');
  assert(create, 'create request missing');
  assert.strictEqual(JSON.parse(create.options.body).tool_call_limit, 3);
  assert.strictEqual(window.document.querySelector('.agent-card h3').textContent, 'Writer');

  window.document.querySelector('[aria-label="Deaktivieren"]')?.click();
  await new Promise(resolve => setTimeout(resolve, 30));
  const update = requests.find(item => item.url.includes('/agents/a2') && item.options.method === 'PUT');
  assert(update, 'disable request missing');
  assert.strictEqual(JSON.parse(update.options.body).enabled, false);
  assert(requests.every(item => item.options.headers.Authorization === 'Bearer secret'));
  window.dispatchEvent(new window.Event('pagehide'));
  console.log('agents UI: 10 checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
