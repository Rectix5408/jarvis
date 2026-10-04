// SPDX-License-Identifier: Apache-2.0
(() => {
  'use strict';
  if (window.parent !== window || document.getElementById('jarvis-shell')) return;
  const paths = ['/dashboard', '/assistant', '/chat', '/wissen', '/settings', '/portal'];
  if (!paths.includes(location.pathname)) return;
  const links = [
    { name: 'Kontrollzentrum', path: '/dashboard', icon: 'orbit' },
    { name: 'Assistent', path: '/assistant', icon: 'messages-square' },
    { name: 'Wissen', path: '/wissen', icon: 'network' },
    { name: 'Benutzer-Chat', path: '/userchat', icon: 'users' },
    { name: 'Portal', path: '/portal', icon: 'layout-grid' },
    { name: 'KI-Profile', path: '/settings#profiles', icon: 'cpu', admin: true },
    { name: 'Agentenrollen', path: '/settings#agent_orchestrator', icon: 'workflow', admin: true },
    { name: 'Tasks', path: '/settings#cron', icon: 'calendar-clock', admin: true },
    { name: 'Skills', path: '/settings#skills', icon: 'puzzle', admin: true },
    { name: 'Einstellungen', path: '/settings', icon: 'settings-2', admin: true },
  ];
  let identity = null, credential = '', refreshing = false, destroyed = false;
  const controllers = new Set();
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text;
    return node;
  };
  const icon = name => {
    const node = el('i'); node.dataset.lucide = name; return node;
  };
  const token = () => {
    try { return localStorage.getItem('jarvis_chat_token') || localStorage.getItem('jarvis_token') || localStorage.getItem('jarvis_uc_token') || ''; }
    catch (_) { return ''; }
  };
  const shell = el('aside', 'jarvis-shell'); shell.id = 'jarvis-shell'; shell.hidden = true;
  shell.setAttribute('aria-label', 'JARVIS Navigation');
  const brand = el('a', 'js-brand', 'JARVIS'); brand.href = '/dashboard';
  const img = el('img'); img.src = '/static/favicon_32.png'; img.alt = ''; img.width = img.height = 28;
  brand.prepend(img); shell.append(brand, el('span', 'js-caption', 'OPERATIONS'));
  const nav = el('nav', 'js-nav'); nav.setAttribute('aria-label', 'Arbeitsbereiche'); shell.append(nav);
  const actions = el('div', 'js-actions');
  function button(label, symbol, action) {
    const node = el('button', 'js-icon'); node.type = 'button'; node.title = label;
    node.setAttribute('aria-label', label); node.append(icon(symbol)); node.addEventListener('click', action); return node;
  }
  const palette = el('dialog', 'js-palette'); palette.setAttribute('aria-label', 'Arbeitsbereich suchen');
  const search = el('input', 'js-search'); search.type = 'search'; search.placeholder = 'Arbeitsbereich suchen'; search.setAttribute('aria-label', search.placeholder);
  const results = el('nav', 'js-results'); results.setAttribute('aria-label', 'Suchergebnisse');
  const closePalette = button('Suche schliessen', 'x', () => palette.close());
  const searchRow = el('div', 'js-search-row'); searchRow.append(icon('search'), search, closePalette); palette.append(searchRow, results);
  const panel = el('dialog', 'js-context'); panel.setAttribute('aria-label', 'Systemstatus');
  const panelTitle = el('div', 'js-context-title'); panelTitle.append(el('h2', '', 'System'), button('Systemstatus schliessen', 'x', () => panel.close()));
  const details = el('dl', 'js-details'); const status = el('p', 'js-status'); status.setAttribute('role', 'status');
  panel.append(panelTitle, details, status);
  const footer = el('footer', 'js-footer'); footer.hidden = true;
  const connection = el('span', '', 'Status unbekannt'), user = el('span'), clock = el('time');
  footer.append(connection, user, clock);
  function openPalette() {
    if (!identity || !pageReady()) return;
    search.value = ''; drawResults(); palette.showModal(); search.focus();
  }
  actions.append(button('Arbeitsbereich suchen', 'search', openPalette), button('Systemstatus', 'activity', () => {
    panel.showModal(); loadStatus();
  }));
  shell.append(actions); document.body.append(shell, footer, palette, panel);
  function pageReady() {
    const login = document.getElementById('login-screen');
    return !login || getComputedStyle(login).display === 'none';
  }
  function available() { return links.filter(link => !link.admin || identity?.is_admin === true); }
  function linkNode(item) {
    const node = el('a', 'js-link'); node.href = item.path; node.append(icon(item.icon), el('span', '', item.name));
    if (location.pathname === item.path.split('#')[0] && (!item.path.includes('#') || location.hash === '#' + item.path.split('#')[1])) node.setAttribute('aria-current', 'page');
    return node;
  }
  function drawResults() {
    results.replaceChildren(...available().filter(item => item.name.toLocaleLowerCase('de').includes(search.value.toLocaleLowerCase('de'))).map(linkNode));
    if (!results.children.length) results.append(el('p', 'js-empty', 'Keine Treffer'));
    window.lucide?.createIcons();
  }
  function drawNav() { nav.replaceChildren(...available().map(linkNode)); drawResults(); }
  function visibility() {
    const visible = !!identity && pageReady();
    shell.hidden = footer.hidden = !visible;
    document.body.classList.toggle('jarvis-shell-ready', visible);
    if (!visible) { palette.close(); panel.close(); }
  }
  async function api(path, auth) {
    const controller = new AbortController(); controllers.add(controller);
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(path, { headers: { Authorization: `Bearer ${auth}` }, signal: controller.signal });
      if (!response.ok) throw Object.assign(new Error(response.status === 401 ? 'Sitzung abgelaufen' : response.status === 403 ? 'Keine Berechtigung' : 'Dienst nicht erreichbar'), { status: response.status });
      return await response.json();
    } finally { clearTimeout(timer); controllers.delete(controller); }
  }
  async function refreshIdentity() {
    if (refreshing || destroyed) return;
    const auth = token();
    if (auth !== credential || !auth) { identity = null; visibility(); }
    credential = auth;
    if (!auth) return;
    refreshing = true;
    try {
      const me = await api('/api/me', auth);
      if (token() !== auth || destroyed) return;
      identity = me; user.textContent = me.username || ''; connection.textContent = 'Backend verbunden';
      drawNav(); visibility();
    } catch (error) {
      if (token() !== auth || destroyed) return;
      // A failed authorization check must also remove stale administrator links.
      identity = null; user.textContent = ''; connection.textContent = error.message; visibility();
    } finally { refreshing = false; }
  }
  async function loadStatus() {
    const auth = credential;
    details.replaceChildren(); status.textContent = 'Pruefe Status ...';
    const values = await Promise.allSettled([api('/api/llm/active-status', auth), api('/api/cpu', auth)]);
    if (auth !== token() || !identity || destroyed) return;
    const add = (name, value) => details.append(el('dt', '', name), el('dd', '', String(value)));
    add('Benutzer', identity.username || '--');
    if (values[0].status === 'fulfilled') {
      const model = values[0].value;
      add('Modellprofil', model.profile_name || '--');
      add('Modellverbindung', { ok: 'Erreichbar', degraded: 'Modell pruefen', down: 'Nicht erreichbar' }[model.status] || 'Unbekannt');
    } else add('Modellverbindung', 'Nicht verfuegbar');
    const cpu = values[1].status === 'fulfilled' ? values[1].value.cpu : null;
    add('CPU', Number.isFinite(cpu) ? `${cpu.toFixed(1)} %` : 'Nicht verfuegbar');
    status.textContent = values.some(value => value.status === 'rejected') ? 'Nicht alle Statusdaten konnten geladen werden.' : '';
    if (values.some(value => value.status === 'rejected' && value.reason.status === 401)) { identity = null; visibility(); }
  }
  search.addEventListener('input', drawResults);
  for (const dialog of [palette, panel]) dialog.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); dialog.close(); }
  });
  search.addEventListener('keydown', event => {
    const first = results.querySelector('a');
    if (event.key === 'ArrowDown' && first) { event.preventDefault(); first.focus(); }
    if (event.key === 'Enter' && first) { event.preventDefault(); first.click(); }
  });
  results.addEventListener('keydown', event => {
    const items = [...results.querySelectorAll('a')], index = items.indexOf(document.activeElement);
    if (['ArrowDown', 'ArrowUp'].includes(event.key) && items.length) {
      event.preventDefault(); items[(index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length].focus();
    }
  });
  document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); openPalette(); }
  });
  window.addEventListener('storage', refreshIdentity); window.addEventListener('focus', refreshIdentity);
  window.addEventListener('hashchange', drawNav);
  const login = document.getElementById('login-screen');
  const observer = new MutationObserver(() => { visibility(); if (!identity) refreshIdentity(); });
  if (login) observer.observe(login, { attributes: true, attributeFilter: ['class', 'style'] });
  const timer = setInterval(() => {
    clock.textContent = new Date().toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
    if (!document.hidden && token() !== credential) refreshIdentity();
  }, 1000);
  const authTimer = setInterval(() => { if (!document.hidden) refreshIdentity(); }, 60000);
  window.addEventListener('pagehide', () => {
    destroyed = true; clearInterval(timer); clearInterval(authTimer); observer.disconnect(); controllers.forEach(controller => controller.abort());
  });
  window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
  refreshIdentity();
})();
