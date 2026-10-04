// SPDX-License-Identifier: Apache-2.0
// API fixtures are confined to this browser test; production never uses them.
const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const { chromium } = createRequire(`${process.env.DASHBOARD_NODE_MODULES || '/tmp/jarvis-dashboard-tools/node_modules'}/package.json`)('playwright');
const base = process.env.DASHBOARD_URL || 'http://127.0.0.1:8767';
let checks = 0;
const check = (condition, label) => { assert.ok(condition, label); checks++; };
(async () => {
  const browser = await chromium.launch();
  try {
    for (const [width, height] of [[1920,1080], [1440,900], [1366,768], [820,1180], [390,844], [320,740]]) {
      const context = await browser.newContext({ viewport: { width, height } });
      await context.addInitScript(() => localStorage.setItem('jarvis_token', 'layout-fixture'));
      await context.route('**/api/**', route => route.fulfill({ json: new URL(route.request().url()).pathname === '/api/me' ? { username: 'Layout fixture', is_admin: true } : { cpu: 12, status: 'ok', profile_name: 'Test profile' } }));
      await context.route('**/*', route => {
        const url = new URL(route.request().url());
        return (route.request().resourceType() === 'script' && !/\/shell\.js|\/lucide\.min\.js/.test(url.pathname)) || url.origin !== base ? route.abort() : route.fallback();
      });
      const page = await context.newPage();
      for (const path of ['/dashboard', '/chat', '/settings', '/wissen', '/portal']) {
        await page.goto(base + path);
        await page.evaluate(path => {
          const login = document.getElementById('login-screen'); if (login) login.style.display = 'none';
          if (path === '/chat') document.getElementById('chat-screen').classList.remove('hidden');
          if (path === '/settings') document.getElementById('settings-modal').classList.add('open');
          if (path === '/wissen') document.getElementById('wi-app').classList.remove('hidden');
        }, path);
        await page.locator('#jarvis-shell').waitFor({ state: 'visible' });
        if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) console.log(path, width, await page.evaluate(() => [...document.querySelectorAll('body *')].map(el => ({ id: el.id, cls: el.className, right: el.getBoundingClientRect().right, width: el.getBoundingClientRect().width })).filter(el => el.width && el.right > innerWidth + 1).slice(0, 12)));
        check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${path} ${width}: no overflow`);
        if (path === '/chat') {
          const input = await page.locator('#msg-input').boundingBox(), nav = await page.locator('#jarvis-shell').boundingBox();
          check(input && (width > 760 ? input.x >= nav.x + nav.width : input.y + input.height <= nav.y), `Composer clear of shell ${width}`);
        }
        await page.locator('button[aria-label="Arbeitsbereich suchen"]').click();
        await page.locator('.js-search').fill('Tasks');
        check(await page.locator('.js-results a').count() === 1, 'Palette finds existing task settings');
        await page.keyboard.press('Escape');
        await page.locator('button[aria-label="Systemstatus"]').click();
        await page.waitForFunction(() => document.querySelector('.js-details').textContent.includes('12.0 %'));
        check(await page.locator('.js-context').isVisible(), 'Authenticated status drawer');
        await page.keyboard.press('Escape');
        if (path === '/dashboard' || path === '/chat') await page.screenshot({ path: `/tmp/jarvis-shell-${path.slice(1)}-${width}.png`, fullPage: true });
      }
      await context.close();
    }
    console.log(`Shell layout: ${checks} assertions passed`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
