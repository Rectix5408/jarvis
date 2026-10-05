// SPDX-License-Identifier: Apache-2.0
const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const dependency = createRequire(`${process.env.DASHBOARD_NODE_MODULES || '/tmp/jarvis-dashboard-tools/node_modules'}/package.json`);
const { chromium } = dependency('playwright');
const base = process.env.DASHBOARD_URL || 'http://127.0.0.1:8769';
let checks = 0;
function check(value, message) { assert.ok(value, message); checks++; }

(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 820, 390, 320]) {
      const context = await browser.newContext({ viewport: { width, height: 900 } });
      await context.addInitScript(() => localStorage.setItem('jarvis_token', 'fixture'));
      let enabled = true;
      const mutations = [];
      const agent = () => ({ id:'a1', name:'Research <script>', description:'Local research', goal:'Pruefe Quellen', instructions:'', profile_id:'', tools:['read_file'], knowledge:[], autonomy:'supervised', max_steps:4, timeout_seconds:30, token_budget:500, cost_budget:0, tool_call_limit:2, permissions:{read:true}, enabled });
      const run = { id:'r1longidentifier', agent_id:'a1', task:'Pruefe die lokale Wissensbasis', status:'RUNNING', route:'local', model:'qwen3:4b', started:Date.now()/1000-7, finished:null, result:'', error:'', input_tokens:14, output_tokens:8, cost:0, tools:['read_file'], events:[{id:1,ts:Date.now()/1000-5,kind:'TOOL_STARTED',message:'read_file',data:{tool:'read_file'}}] };
      await context.route('**/api/**', route => {
        const request = route.request(), path = new URL(request.url()).pathname;
        if (path === '/api/me') return route.fulfill({json:{username:'Admin',is_admin:true}});
        if (!path.startsWith('/api/operations')) return route.fulfill({json:{}});
        if (request.method() !== 'GET') {
          mutations.push({path, body:request.postDataJSON()});
          if (path.endsWith('/a1') && request.method() === 'PUT') enabled = request.postDataJSON().enabled;
          return route.fulfill({json:path.endsWith('/stop') ? {...run,status:'CANCELLED'} : agent()});
        }
        if (path.endsWith('/metadata')) return route.fulfill({json:{tools:['read_file','write_file'],profiles:[],autonomy:['manual','supervised','autonomous']}});
        if (path.endsWith('/approvals')) return route.fulfill({json:[]});
        if (path.endsWith('/runs/r1longidentifier')) return route.fulfill({json:run});
        if (path.endsWith('/runs')) return route.fulfill({json:[run]});
        if (path.endsWith('/agents')) return route.fulfill({json:[agent()]});
        return route.fulfill({json:{}});
      });
      const page = await context.newPage(), errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(base + '/agents');
      await page.locator('.agent-card').waitFor();
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `No overflow at ${width}`);
      check(await page.locator('.agent-card script').count() === 0, 'Agent data rendered as text');
      check(await page.locator('#metric-active').textContent() === '1', 'Active metric is backend-derived');
      check(await page.locator('#metric-running').textContent() === '1', 'Running metric is backend-derived');
      await page.getByRole('button', {name:'Lauf oeffnen'}).click();
      await page.locator('#run-inspector[open]').waitFor();
      check(await page.locator('#run-facts').textContent().then(text => text.includes('qwen3:4b')), 'Inspector uses persisted model');
      await page.getByRole('button', {name:'Tools', exact:true}).click();
      check(await page.locator('#run-tools').textContent().then(text => text.includes('read_file')), 'Tool timeline is real event data');
      await page.getByRole('button', {name:'Lauf stoppen'}).click();
      await page.waitForTimeout(40);
      check(mutations.some(item => item.path.endsWith('/runs/r1longidentifier/stop')), 'Stop reaches backend');
      await page.screenshot({path:`/tmp/jarvis-agents-${width}.png`,fullPage:true});
      check(errors.length === 0, `No page errors: ${errors}`);
      await context.close();
    }
    console.log(`Agent Studio browser: ${checks} assertions passed`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
