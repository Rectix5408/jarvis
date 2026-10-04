// SPDX-License-Identifier: Apache-2.0
const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const dependency = createRequire(`${process.env.DASHBOARD_NODE_MODULES || '/tmp/jarvis-dashboard-tools/node_modules'}/package.json`);
const { chromium } = dependency('playwright'); const { PNG } = dependency('pngjs');
const base = process.env.DASHBOARD_URL || 'http://127.0.0.1:8768';
let checks = 0;
const check = (condition, message) => { assert.ok(condition, message); checks++; };
(async () => {
  const browser = await chromium.launch();
  try {
    for (const [width,height] of [[1920,1080],[1440,900],[1366,768],[820,1180],[390,844]]) {
      const context = await browser.newContext({ viewport: { width,height } });
      await context.addInitScript(() => localStorage.setItem('jarvis_token', 'ui-fixture'));
      await context.route('**/api/**', route => route.fulfill({ json: { username: 'Rene', is_admin: false } }));
      await context.route('**/*', route => {
        const url = new URL(route.request().url());
        return url.origin !== base || (route.request().resourceType() === 'script' && !/shell\.js|lucide\.min\.js|assistant\.js|core_geometry\.js|\/three\./.test(url.pathname)) ? route.abort() : route.fallback();
      });
      const page = await context.newPage(); const errors = []; page.on('pageerror', error => errors.push(error.message));
      await page.goto(base + '/assistant');
      await page.evaluate(() => {
        document.getElementById('login-screen').style.display = 'none'; document.getElementById('chat-screen').classList.remove('hidden');
        if (innerWidth <= 1100) document.getElementById('chat-screen').classList.add('sidebar-collapsed');
      });
      await page.locator('#jarvis-shell').waitFor({ state: 'visible' });
      await page.waitForFunction(() => document.querySelector('.assistant-context'));
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `No overflow ${width}`);
      const composer = await page.locator('#msg-input').boundingBox();
      check(composer && composer.y + composer.height <= height - (width <= 760 ? 64 : 32), `Composer fits ${width}`);
      check(await page.locator('#btn-mic').count() === 1 && await page.locator('#chat-sidebar').count() === 1, 'Existing chat controls reused');
      await page.evaluate(() => {
        window.dispatchEvent(new CustomEvent('jarvis:activity', { detail: { state: 'working' } }));
        window.dispatchEvent(new CustomEvent('jarvis:status', { detail: { message: '<img src=x onerror=alert(1)> Status fixture' } }));
      });
      check(await page.locator('.assistant-core-state').textContent() === 'Arbeitet', 'Activity event connected');
      check(await page.locator('.assistant-context-detail img').count() === 0, 'Status is text, not HTML');
      if (width > 1100) {
        await page.waitForFunction(() => Number(document.querySelector('.assistant-core').dataset.frames) > 2);
        const frames = await page.locator('.assistant-core').getAttribute('data-frames');
        await page.waitForTimeout(150);
        check(Number(await page.locator('.assistant-core').getAttribute('data-frames')) > Number(frames), 'Core moves');
        const image = PNG.sync.read(await page.locator('.assistant-core canvas').screenshot());
        let lit = 0; for(let i=0;i<image.data.length;i+=4) if(image.data[i+1] > 80 && image.data[i+2] > 50) lit++;
        check(lit > 100, 'Canvas is nonblank');
        await page.emulateMedia({ reducedMotion: 'reduce' }); await page.waitForTimeout(100);
        const reducedFrames = await page.locator('.assistant-core').getAttribute('data-frames'); await page.waitForTimeout(150);
        check(await page.locator('.assistant-core').getAttribute('data-frames') === reducedFrames, 'Reduced motion stops continuous rendering');
      } else check(await page.locator('.assistant-context').isHidden(), 'Compact layout prioritizes conversation');
      check(!errors.length, `No JS errors: ${errors}`);
      await page.screenshot({ path: `/tmp/jarvis-assistant-${width}.png`, fullPage: true }); await context.close();
    }
    console.log(`Assistant: ${checks} assertions passed`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
