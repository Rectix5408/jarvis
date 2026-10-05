// SPDX-License-Identifier: Apache-2.0
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const credential = () => localStorage.getItem('jarvis_chat_token') || localStorage.getItem('jarvis_token') || localStorage.getItem('jarvis_uc_token') || '';
  let agents = [], runs = [], approvals = [], metadata = { tools: [], profiles: [] }, selectedRun = '', busy = false, disposed = false, pollTimer;
  const controllers = new Set();
  function element(tag, text, cls) { const node = document.createElement(tag); node.textContent = text || ''; if (cls) node.className = cls; return node; }
  function icon(name) { const node = element('i'); node.dataset.lucide = name; return node; }
  function control(label, symbol, handler, cls = '') { const button = element('button', '', cls); button.type = 'button'; button.title = label; button.setAttribute('aria-label', label); button.append(icon(symbol)); button.addEventListener('click', handler); return button; }
  async function api(path, options = {}) {
    const controller = new AbortController(); controllers.add(controller);
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch('/api/operations' + path, { ...options, headers: { Authorization: `Bearer ${credential()}`, 'Content-Type': 'application/json', ...(options.headers || {}) }, signal: controller.signal });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Anfrage fehlgeschlagen');
      return data;
    } finally { clearTimeout(timer); controllers.delete(controller); }
  }
  const status = message => { $('agents-status').textContent = message || ''; };
  function renderAgents() {
    const list = $('agent-list'); list.replaceChildren(); $('agent-count').textContent = String(agents.length);
    if (!agents.length) list.append(element('p', 'Noch keine Agenten angelegt.', 'empty'));
    for (const agent of agents) {
      const card = element('article', '', 'agent-card');
      card.append(element('h3', agent.name), element('p', agent.description || agent.goal));
      const meta = element('div', '', 'agent-meta');
      for (const value of [agent.enabled ? 'AKTIV' : 'DEAKTIVIERT', agent.autonomy.toUpperCase(), `${agent.tools.length} TOOLS`, `${agent.max_steps} SCHRITTE`]) meta.append(element('span', value));
      card.append(meta);
      const actions = element('div', '', 'agent-actions');
      actions.append(control('Agent starten', 'play', () => openRun(agent)), control('Bearbeiten', 'pencil', () => openEditor(agent)), control('Duplizieren', 'copy', () => mutate(`/agents/${agent.id}/duplicate`, { method: 'POST' })), control(agent.enabled ? 'Deaktivieren' : 'Aktivieren', agent.enabled ? 'power' : 'power-off', () => toggleAgent(agent), agent.enabled ? 'danger' : ''));
      actions.firstChild.disabled = !agent.enabled; card.append(actions); list.append(card);
    }
    window.lucide?.createIcons();
  }
  function renderMetrics() {
    $('metric-active').textContent = String(agents.filter(agent => agent.enabled).length);
    $('metric-running').textContent = String(runs.filter(run => run.status === 'RUNNING').length);
    $('metric-waiting').textContent = String(runs.filter(run => ['QUEUED','WAITING_FOR_APPROVAL'].includes(run.status)).length);
    $('metric-failed').textContent = String(runs.filter(run => ['FAILED','INTERRUPTED'].includes(run.status)).length);
  }
  function renderRuns() {
    const list = $('run-list'); list.replaceChildren();
    if (!runs.length) list.append(element('p', 'Noch keine Laeufe.', 'empty'));
    const names = Object.fromEntries(agents.map(agent => [agent.id, agent.name]));
    for (const run of runs) {
      const row = element('article', '', 'run-row');
      const state = element('span', run.status, 'run-status'); state.dataset.status = run.status;
      row.append(state, element('span', `${names[run.agent_id] || 'Agent'} · ${run.task}`, 'run-task'));
      const when = element('time', new Date(run.started * 1000).toLocaleString()); when.dateTime = new Date(run.started * 1000).toISOString(); row.append(when);
      row.append(control('Lauf oeffnen', 'panel-right-open', () => inspect(run.id))); list.append(row);
    }
    window.lucide?.createIcons();
  }
  function renderApprovals() {
    const list = $('approval-list'); list.replaceChildren(); $('approval-count').textContent = String(approvals.length);
    if (!approvals.length) list.append(element('p', 'Keine offenen Freigaben.', 'empty'));
    for (const approval of approvals) {
      const row = element('article', '', 'approval-row');
      const copy = element('div'); copy.append(element('strong', approval.tool), element('p', approval.summary)); row.append(copy);
      const actions = element('div'); actions.append(control('Ablehnen', 'x', () => decideApproval(approval.id, false), 'danger'), control('Genehmigen', 'check', () => decideApproval(approval.id, true), 'approve')); row.append(actions); list.append(row);
    }
    window.lucide?.createIcons();
  }
  async function load(show = false) {
    if (busy || disposed || document.hidden) return;
    try {
      const [agentData, runData, approvalData, meta] = await Promise.all([api('/agents'), api('/runs'), api('/approvals?pending=true'), api('/metadata')]);
      agents = agentData; runs = runData; approvals = approvalData; metadata = meta; renderAgents(); renderRuns(); renderApprovals(); renderMetrics();
      if (show) status('Aktualisiert.'); else if ($('agents-status').textContent === 'Agenten werden geladen.') status('');
      if (selectedRun) await inspect(selectedRun, false);
    } catch (error) { status(error.name === 'AbortError' ? 'Zeitlimit erreicht.' : error.message); }
  }
  function toolChoices(selected = []) {
    const selectedSet = new Set(selected); const container = $('agent-tools'); container.replaceChildren();
    for (const name of metadata.tools) {
      const label = element('label'), input = document.createElement('input'); input.type = 'checkbox'; input.value = name; input.checked = selectedSet.has(name); label.append(input, document.createTextNode(name)); container.append(label);
    }
  }
  function openEditor(agent = null) {
    $('agent-form').reset(); $('agent-id').value = agent?.id || ''; $('agent-form-title').textContent = agent ? 'Agent bearbeiten' : 'Agent erstellen';
    $('agent-name').value = agent?.name || ''; $('agent-description').value = agent?.description || ''; $('agent-goal').value = agent?.goal || ''; $('agent-instructions').value = agent?.instructions || '';
    const profiles = $('agent-profile'); profiles.replaceChildren(new Option('Benutzerprofil erben', ''));
    metadata.profiles.forEach(profile => profiles.append(new Option(`${profile.name} · ${profile.model}`, profile.id)));
    profiles.value = agent?.profile_id || ''; $('agent-autonomy').value = agent?.autonomy || 'supervised'; $('agent-enabled').checked = agent?.enabled !== false;
    $('agent-steps').value = agent?.max_steps || 12; $('agent-timeout').value = agent?.timeout_seconds || 600; $('agent-token-budget').value = agent?.token_budget || 0; $('agent-tool-limit').value = agent?.tool_call_limit || 0; $('agent-cost-budget').value = agent?.cost_budget || 0;
    $('agent-knowledge').value = (agent?.knowledge || []).join(', '); toolChoices(agent?.tools || []);
    document.querySelectorAll('[data-permission]').forEach(input => { input.checked = agent?.permissions?.[input.dataset.permission] ?? input.dataset.permission === 'read'; });
    $('agent-editor').showModal();
  }
  function agentBody() {
    const permissions = {}; document.querySelectorAll('[data-permission]').forEach(input => permissions[input.dataset.permission] = input.checked);
    return { name: $('agent-name').value, description: $('agent-description').value, goal: $('agent-goal').value, instructions: $('agent-instructions').value,
      profile_id: $('agent-profile').value, autonomy: $('agent-autonomy').value, enabled: $('agent-enabled').checked,
      max_steps: Number($('agent-steps').value), timeout_seconds: Number($('agent-timeout').value), token_budget: Number($('agent-token-budget').value), tool_call_limit: Number($('agent-tool-limit').value), cost_budget: Number($('agent-cost-budget').value),
      tools: [...$('agent-tools').querySelectorAll('input:checked')].map(input => input.value), knowledge: $('agent-knowledge').value.split(',').map(value => value.trim()).filter(Boolean), permissions };
  }
  async function mutate(path, options, close) {
    if (busy) return;
    busy = true;
    try { await api(path, options); close?.close(); status('Gespeichert.'); }
    catch (error) { status(error.message); }
    finally { busy = false; await load(); }
  }
  async function toggleAgent(agent) {
    await mutate(`/agents/${agent.id}`, { method: 'PUT', body: JSON.stringify({ ...agent, enabled: !agent.enabled }) });
  }
  async function decideApproval(identifier, approved) {
    await mutate(`/approvals/${identifier}/decision`, { method: 'POST', body: JSON.stringify({ approved }) });
  }
  function openRun(agent) { $('run-agent-id').value = agent.id; $('run-task').value = ''; $('run-dialog').showModal(); $('run-task').focus(); }
  async function inspect(identifier, open = true) {
    try {
      const run = await api('/runs/' + identifier); selectedRun = identifier;
      $('run-title').textContent = `Run ${identifier.slice(0, 8)}`;
      const duration = Math.max(0, Math.round(((run.finished || Date.now() / 1000) - run.started)));
      const values = [['Status', run.status], ['Route', run.route || 'Noch nicht bekannt'], ['Modell', run.model || 'Noch nicht bekannt'], ['Dauer', `${duration} s`], ['Tokens', `${run.input_tokens + run.output_tokens}`], ['Kosten', Number(run.cost || 0).toFixed(4)]];
      $('run-facts').replaceChildren(...values.map(([name, value]) => { const pair = element('div'); pair.append(element('dt', name), element('dd', String(value))); return pair; }));
      $('run-events').replaceChildren(...run.events.map(event => { const row = element('li'); row.append(element('time', new Date(event.ts * 1000).toLocaleTimeString()), element('span', event.kind), element('span', event.message)); return row; }));
      $('run-tools').replaceChildren(...run.events.filter(event => event.kind.startsWith('TOOL_')).map(event => element('li', `${event.kind}: ${event.message}`)));
      $('run-task-detail').textContent = run.task;
      $('run-result').textContent = run.error || run.result || 'Noch kein Ergebnis.';
      $('run-errors').textContent = run.error || 'Keine Fehler protokolliert.';
      $('stop-run').hidden = !['QUEUED','RUNNING','WAITING_FOR_APPROVAL'].includes(run.status);
      if (open && !$('run-inspector').open) $('run-inspector').showModal();
    } catch (error) { status(error.message); selectedRun = ''; }
  }
  $('new-agent').addEventListener('click', () => openEditor());
  $('refresh-runs').addEventListener('click', () => load(true));
  $('agent-form').addEventListener('submit', event => { event.preventDefault(); const id = $('agent-id').value; mutate(id ? `/agents/${id}` : '/agents', { method: id ? 'PUT' : 'POST', body: JSON.stringify(agentBody()) }, $('agent-editor')); });
  $('run-form').addEventListener('submit', event => { event.preventDefault(); const id = $('run-agent-id').value; mutate(`/agents/${id}/runs`, { method: 'POST', body: JSON.stringify({ task: $('run-task').value }) }, $('run-dialog')); });
  $('close-run').addEventListener('click', () => { selectedRun = ''; $('run-inspector').close(); });
  $('stop-run').addEventListener('click', () => mutate(`/runs/${selectedRun}/stop`, { method: 'POST' }));
  document.querySelectorAll('[data-run-tab]').forEach(button => button.addEventListener('click', () => {
    document.querySelectorAll('[data-run-tab]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    document.querySelectorAll('[data-run-panel]').forEach(panel => panel.hidden = panel.dataset.runPanel !== button.dataset.runTab);
  }));
  document.addEventListener('visibilitychange', load);
  window.addEventListener('pagehide', () => { disposed = true; clearInterval(pollTimer); controllers.forEach(controller => controller.abort()); });
  load(); pollTimer = setInterval(() => { if (approvals.length || runs.some(run => ['QUEUED','RUNNING','WAITING_FOR_APPROVAL'].includes(run.status)) || selectedRun) load(); }, 2000);
})();
