// SPDX-License-Identifier: Apache-2.0
const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const dependency = createRequire(`${process.env.DASHBOARD_NODE_MODULES || '/tmp/jarvis-dashboard-tools/node_modules'}/package.json`);
const { chromium } = dependency('playwright');
const base = process.env.DASHBOARD_URL || 'http://127.0.0.1:8769';
let checks = 0;
function check(value, message) { assert.ok(value, message); checks++; }
const registry = [
  { runtime_id:'qwen3.5:2b',display_name:'Qwen 3.5 2B',family:'Qwen',parameters:'2B',quantization:null,context_length:8192,disk_size_bytes:2700000000,estimated_ram_bytes:3500000000,capabilities:{chat:true,tool_calling:true,vision:null},recommended_roles:['fast'],compatibility:{state:'compatible',reasons:[]} },
  { runtime_id:'qwen3.5:4b',display_name:'Qwen 3.5 4B',family:'Qwen',parameters:'4B',quantization:'Q4',context_length:8192,disk_size_bytes:3400000000,estimated_ram_bytes:5000000000,capabilities:{chat:true,tool_calling:true},recommended_roles:['general'],compatibility:{state:'warning',reasons:['INSUFFICIENT_RAM']} },
  { runtime_id:'large:14b',display_name:'Large 14B',disk_size_bytes:9000000000,estimated_ram_bytes:18000000000,capabilities:{chat:true},recommended_roles:['strong'],compatibility:{state:'incompatible',reasons:['INSUFFICIENT_RAM_HARD']} },
  { runtime_id:'unknown:model',display_name:'Unknown Model',capabilities:{chat:true},recommended_roles:[],compatibility:{state:'unknown',reasons:['UNKNOWN_REQUIREMENTS']} },
];
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [1440, 820, 390, 320]) {
      const context = await browser.newContext({ viewport: { width, height: 900 } });
      await context.addInitScript(() => localStorage.setItem('jarvis_token','fixture'));
      let online=true, authorized=true, rejectRole=false, roles={fast:'qwen3.5:2b',general:'qwen3.5:4b',strong:''};
      let installed=[{name:'qwen3.5:2b',size:2700000000},{name:'qwen3.5:4b',size:3400000000}], running=[], jobs=[], statusCalls=0;
      const mutations=[];
      await context.route('**/api/**', async route => {
        const request=route.request(), path=new URL(request.url()).pathname, method=request.method();
        if (!authorized) return route.fulfill({status:403,json:{detail:'Forbidden'}});
        let json;
        if(path==='/api/me') json={username:'Admin',is_admin:true};
        else if(path.endsWith('/status')) { statusCalls++; json={runtime:'ollama',online,version:'0.35.1',max_loaded_models:1,installed_models:online?installed:[],models:online?installed:[],running_models:running.map(name=>({name})),hardware:{architecture:'x86_64',cpu:{logical_cpus:4,model:'AMD EPYC Rome'},memory:{total:7800000000,available:5800000000},disk:{total:193000000000,available:120000000000},gpu:{type:'none',vram_total:null}}}; }
        else if(path.endsWith('/registry')) json=registry.map(model=>online?model:{...model,compatibility:{state:'incompatible',reasons:['RUNTIME_UNAVAILABLE']}});
        else if(path.endsWith('/downloads')) json=jobs;
        else if(path.endsWith('/usage')) json={today:{requests:4,routes:{local:3,cloud:1},local_rate:75}};
        else if(path.endsWith('/routing')&&method==='GET') json={mode:'smart',local_model:roles.general,local_fast_model:roles.fast,local_general_model:roles.general,local_strong_model:roles.strong};
        else {
          const body=request.postDataJSON(); mutations.push({path,body});
          if(path.endsWith('/roles')) { if(rejectRole) return route.fulfill({status:409,json:{detail:{code:'MODEL_INCOMPATIBLE'}}}); roles[body.role]=body.model; }
          if(path.endsWith('/pull')) jobs=[{job_id:'a'.repeat(32),model_id:body.model,state:'DOWNLOADING',phase:'pulling layer',bytes_completed:47,bytes_total:null,created_at:1,updated_at:2}];
          if(path.endsWith('/cancel')) jobs=[{...jobs[0],state:'CANCELLED',phase:'Downloadstream beendet'}];
          if(path.endsWith('/load')) running=[body.model];
          if(path.endsWith('/unload')) running=[];
          if(path.endsWith('/delete')) installed=installed.filter(model=>model.name!==body.model);
          json=path.endsWith('/test')?{success:true,response:'OK',latency_ms:42}:{success:true};
        }
        return route.fulfill({json});
      });
      const page=await context.newPage(), pageErrors=[]; page.on('pageerror',error=>pageErrors.push(error.message)); page.on('dialog',dialog=>dialog.accept());
      await page.goto(base+'/models'); await page.locator('#models-content').waitFor({state:'visible'}); await page.locator('#jarvis-shell').waitFor({state:'visible'});
      check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`No overflow ${width}`);
      check((await page.locator('#runtime-status').textContent()).includes('ONLINE'),'Runtime online');
      check((await page.locator('#hardware').textContent()).includes('AMD EPYC Rome'),'Hardware rendered');
      check(await page.locator('.model-card').count()===4,'Registry models rendered');
      check((await page.locator('.model-card').nth(1).textContent()).includes('warning'),'Warning shown');
      check((await page.locator('.model-card').nth(2).textContent()).includes('incompatible'),'Incompatible shown');
      check((await page.locator('.model-card').nth(3).textContent()).includes('unknown'),'Unknown shown');
      check(await page.locator('#local-strong-model option').first().textContent()==='Cloud','Strong Cloud option');
      check(await page.getByRole('button',{name:'Modell laden'}).first().isEnabled(),'Stopped model can load');
      await page.getByRole('button',{name:'Modell laden'}).first().click(); await page.waitForFunction(()=>document.body.textContent.includes('Loaded'));
      check(running.length===1,'Load uses backend and refreshes');
      await page.getByRole('button',{name:'Modell testen'}).first().click(); await page.locator('#result-dialog').waitFor({state:'visible'}); check((await page.locator('#result-content').textContent()).includes('42 ms'),'Test result shown'); await page.locator('#close-result').click();
      rejectRole=true; await page.locator('#local-fast-model').selectOption('qwen3.5:4b'); await page.waitForTimeout(100); check(await page.locator('#local-fast-model').inputValue()==='qwen3.5:2b','Role failure rolls back'); rejectRole=false;
      const download=page.locator('button[aria-label="Modell herunterladen"]:not(:disabled)').first();
      await download.click(); await page.locator('#confirm-dialog').waitFor({state:'visible'}); await page.locator('#confirm-action').click(); await page.waitForFunction(()=>document.querySelector('#model-downloads progress'));
      check(await page.locator('#model-downloads progress').first().getAttribute('value')===null,'Unknown total is indeterminate');
      jobs=[{...jobs[0],state:'VERIFYING',phase:'Runtime verification',bytes_total:100,bytes_completed:80}]; await page.locator('#refresh-models').click(); await page.waitForFunction(()=>document.querySelector('#model-downloads').textContent.includes('VERIFYING')); check(await page.locator('#model-downloads progress').first().getAttribute('value')==='80','Known progress shown');
      await page.getByRole('button',{name:'Download abbrechen'}).click(); await page.locator('#confirm-action').click(); await page.waitForFunction(()=>document.querySelector('#model-downloads').textContent.includes('CANCELLED')); check(mutations.some(item=>item.path.endsWith('/cancel')),'Cancel reconciles backend');
      check(await page.getByRole('button',{name:/Loeschen blockiert/}).count()>=1,'Assigned delete blocked');
      await page.screenshot({path:`/tmp/jarvis-models-${width}.png`,fullPage:true});
      online=false; await page.locator('#refresh-models').click(); await page.waitForFunction(()=>document.querySelector('#runtime-status').dataset.state==='offline'); check((await page.locator('#runtime-status').textContent()).includes('OFFLINE'),'Offline state');
      const before=statusCalls; await page.waitForTimeout(300); check(statusCalls-before<=1,'No request storm');
      authorized=false; await page.locator('#refresh-models').click(); await page.locator('#models-login').waitFor({state:'visible'}); check(await page.locator('#models-content').isHidden(),'Unauthorized clears controls');
      check(!pageErrors.length,`No JS errors: ${pageErrors}`); await context.close();
    }
    console.log(`Local models UI: ${checks} assertions passed`);
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
