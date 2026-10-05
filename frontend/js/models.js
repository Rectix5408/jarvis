// SPDX-License-Identifier: Apache-2.0
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const token = () => localStorage.getItem('jarvis_chat_token') || localStorage.getItem('jarvis_token') || localStorage.getItem('jarvis_uc_token') || '';
  let disposed = false, busy = false, refreshing = false, routingDirty = false, timer, lastSnapshot = '';
  const controllers = new Set();
  const bytes = n => Number.isFinite(n) ? `${(n / 1e9).toFixed(1)} GB` : 'Unbekannt';
  function node(tag, text, cls) { const el = document.createElement(tag); el.textContent = text || ''; if (cls) el.className = cls; return el; }
  async function api(path, body) {
    const controller = new AbortController(); controllers.add(controller);
    const timeout = setTimeout(() => controller.abort(), body && /test|activate/.test(path) ? 200000 : 20000);
    try {
      const response = await fetch('/api/local-ai/' + path, {
        method: body ? 'POST' : 'GET', headers: { Authorization: `Bearer ${token()}`, 'Content-Type': 'application/json' },
        ...(body ? { body: JSON.stringify(body) } : {}), signal: controller.signal,
      });
      if (response.status === 401 || response.status === 403) {
        $('models-content').hidden = true; $('models-login').hidden = false;
        throw new Error(response.status === 403 ? 'Administratorrechte erforderlich.' : 'Bitte anmelden.');
      }
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Anfrage fehlgeschlagen.');
      return data;
    } finally { clearTimeout(timeout); controllers.delete(controller); }
  }
  function button(label, icon, action, disabled = false) {
    const el = node('button', '', 'model-icon'); el.title = label; el.setAttribute('aria-label', label); el.disabled = disabled;
    const symbol = node('i'); symbol.dataset.lucide = icon; el.append(symbol); el.addEventListener('click', action); return el;
  }
  function row(name, description) {
    const el = node('article', '', 'model-row'), text = node('div');
    text.append(node('h3', name, 'model-name'), node('p', description, 'model-meta')); el.append(text);
    return { el, text };
  }
  async function action(path, model) {
    if (busy) return;
    if (path === 'delete' && !confirm(`${model} aus der lokalen Runtime entfernen?`)) return;
    if (path === 'activate' && !confirm(`${model} als globales Standardprofil aktivieren? Persoenliche Profilwahlen bleiben erhalten.`)) return;
    busy = true;
    document.querySelectorAll('.model-actions button').forEach(button => button.disabled = true);
    $('models-message').textContent = path === 'test' || path === 'activate' ? 'Lokaler Antworttest laeuft.' : 'Anfrage wird verarbeitet.';
    try {
      const body = path === 'routing' ? {
        mode: $('routing-mode').value,
        local_model: $('local-general-model').value,
        local_fast_model: $('local-fast-model').value,
        local_general_model: $('local-general-model').value,
        local_strong_model: $('local-strong-model').value,
        smart_local_complexity_limit: Number($('smart-local-limit').value),
        smart_tool_cloud_complexity: Number($('smart-tool-limit').value),
        daily_cloud_token_budget: Number($('daily-cloud-budget').value),
        monthly_cloud_cost_budget: Number($('monthly-cloud-budget').value),
      } : { model };
      const result = await api(path, body);
      if (path === 'routing') routingDirty = false;
      if (path === 'details') {
        $('model-details-content').textContent = JSON.stringify(result, null, 2); $('model-details').showModal();
      }
      $('models-message').textContent = path === 'test' ? `${result.response} · ${result.latency_ms} ms · lokal` : path === 'activate' ? 'Lokales Routing-Modell ausgewaehlt.' : path === 'routing' ? 'Routing-Richtlinie gespeichert.' : path === 'pull' ? 'Installation gestartet.' : path === 'delete' ? 'Modell entfernt.' : '';
    } catch (error) { $('models-message').textContent = error.name === 'AbortError' ? 'Zeitlimit erreicht; Status erneut pruefen.' : error.message; }
    finally { busy = false; lastSnapshot = ''; if (!disposed) await refresh(); }
  }
  async function refresh() {
    if (refreshing || disposed || busy || document.hidden) return;
    refreshing = true;
    try {
      const status = await api('status'), jobs = await api('downloads'), catalog = await api('catalog'), routing = await api('routing'), usage = await api('usage');
      if (disposed) return;
      $('models-content').hidden = false; $('models-login').hidden = true;
      if ($('models-message').textContent === 'Verbindung wird geprueft.') $('models-message').textContent = '';
      $('runtime-status').textContent = status.online ? `Online · ${status.version || 'Version unbekannt'}` : status.error || 'Offline';
      $('runtime-status').dataset.online = String(status.online);
      if (!routingDirty) {
        $('routing-mode').value = routing.mode;
        $('routing-mode').dataset.localModel = routing.local_model || '';
        const installedNames = status.models.map(model => model.name);
        for (const [id, selected] of [
          ['local-fast-model', routing.local_fast_model],
          ['local-general-model', routing.local_general_model || routing.local_model],
          ['local-strong-model', routing.local_strong_model],
        ]) {
          const select = $(id);
          select.replaceChildren(node('option', 'Nicht zugewiesen'));
          select.firstElementChild.value = '';
          for (const name of installedNames) { const option = node('option', name); option.value = name; select.append(option); }
          select.value = installedNames.includes(selected) ? selected : '';
        }
        $('smart-local-limit').value = routing.smart_local_complexity_limit ?? 0.86;
        $('smart-tool-limit').value = routing.smart_tool_cloud_complexity ?? 0.55;
        $('daily-cloud-budget').value = routing.daily_cloud_token_budget ?? 0;
        $('monthly-cloud-budget').value = routing.monthly_cloud_cost_budget ?? 0;
      }
      const today = usage.today || {}, routes = today.routes || {};
      const metrics = [
        ['Anfragen', today.requests || 0], ['Lokal', routes.local || 0],
        ['Deterministisch', routes.deterministic || 0], ['Cloud', routes.cloud || 0],
        ['Lokalrate', `${today.local_rate || 0} %`],
      ];
      $('usage-metrics').replaceChildren(...metrics.map(([label, value]) => {
        const item = node('div', '', 'usage-metric'); item.append(node('strong', String(value)), node('span', label)); return item;
      }));
      const h = status.hardware;
      const values = [['CPU (JARVIS-Host)', `${h.cpu_percent ?? '?'} % · ${h.cpu_count ?? '?'} Kerne`], ['RAM frei / gesamt', `${bytes(h.ram_available)} / ${bytes(h.ram_total)}`], ['Modell-Volume frei', h.disk ? bytes(h.disk.free) : h.disk_error], ['GPU / VRAM', 'Nicht ermittelt']];
      $('hardware').replaceChildren(...values.map(([name, value]) => { const pair = node('div'); pair.append(node('dt', name), node('dd', value)); return pair; }));
      const snapshot = JSON.stringify([status.models, jobs, catalog, status.online, !!h.disk]);
      // Keep keyboard focus stable when a poll has no changes to the model lists.
      if (snapshot === lastSnapshot) return;
      lastSnapshot = snapshot;
      const installing = jobs.some(job => ['QUEUED','DOWNLOADING','VERIFYING','TESTING'].includes(job.status));
      $('installed-models').replaceChildren(...status.models.map(model => {
        const r = row(model.name, `${bytes(model.size)} · ${model.details?.parameter_size || ''} · ${model.details?.quantization_level || ''}`);
        const actions = node('div', '', 'model-actions');
        for (const [path, label, icon] of [['activate','Als lokales Routing-Modell verwenden','check'],['test','Modell testen','play'],['details','Details','info'],['delete','Modell entfernen','trash-2']]) {
          const control = button(label, icon, () => action(path, model.name), !status.online || (path === 'delete' && installing));
          if (path === 'delete') control.classList.add('danger'); actions.append(control);
        }
        r.el.append(actions); return r.el;
      }));
      if (!status.models.length) $('installed-models').append(node('p', status.online ? 'Keine Modelle installiert.' : 'Modellliste nicht erreichbar.', 'model-meta'));
      $('model-downloads').replaceChildren(...jobs.map(job => {
        const r = row(job.model, `${job.status} · ${job.detail}`);
        if (job.total > 0 && job.status === 'DOWNLOADING') {
          r.text.append(node('p', `Aktuelle Schicht: ${bytes(job.completed)} / ${bytes(job.total)}`, 'model-meta'));
          const progress = node('progress'); progress.max = job.total; progress.value = job.completed; progress.setAttribute('aria-label', 'Download der aktuellen Modellschicht'); r.text.append(progress);
        }
        return r.el;
      }));
      if (!jobs.length) $('model-downloads').append(node('p', 'Keine Installationen.', 'model-meta'));
      $('model-catalog').replaceChildren(...catalog.map(model => {
        const r = row(model.name, `${model.description} · Download ca. ${bytes(model.size_estimate)} · ${model.license}`);
        const source = node('a', 'Modell und Lizenz'); source.href = model.source; source.target = '_blank'; source.rel = 'noopener noreferrer'; r.text.append(source);
        const actions = node('div', '', 'model-actions'); actions.append(button('Installieren', 'download', () => action('pull', model.name), !status.online || !h.disk || installing)); r.el.append(actions); return r.el;
      }));
      window.lucide?.createIcons();
    } catch (error) {
      if (!disposed) {
        $('models-message').textContent = error.message; lastSnapshot = '';
        $('runtime-status').textContent = 'Status nicht erreichbar'; $('runtime-status').dataset.online = 'false';
        document.querySelectorAll('.model-actions button').forEach(button => button.disabled = true);
      }
    }
    finally { refreshing = false; }
  }
  $('refresh-models').addEventListener('click', refresh);
  $('save-routing').addEventListener('click', () => action('routing', $('routing-mode').dataset.localModel || ''));
  document.querySelectorAll('.routing-controls select, .routing-controls input').forEach(control => {
    control.addEventListener('change', () => { routingDirty = true; });
  });
  $('close-model-details').addEventListener('click', () => $('model-details').close());
  document.addEventListener('visibilitychange', refresh);
  window.addEventListener('pagehide', () => { disposed = true; clearInterval(timer); controllers.forEach(controller => controller.abort()); });
  refresh(); timer = setInterval(refresh, 3000);
})();
