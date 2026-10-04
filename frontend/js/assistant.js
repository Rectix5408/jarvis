// SPDX-License-Identifier: Apache-2.0
import * as THREE from 'three';
import { createCore } from '/static/js/core_geometry.js';

if (location.pathname === '/assistant') {
  document.body.classList.add('assistant-workspace');
  const main = document.getElementById('chat-main');
  const context = document.createElement('aside'); context.className = 'assistant-context'; context.setAttribute('aria-label', 'JARVIS Aktivitaet');
  const title = document.createElement('h2'); title.textContent = 'JARVIS';
  const stage = document.createElement('div'); stage.className = 'assistant-core';
  const status = document.createElement('p'); status.className = 'assistant-core-state'; status.setAttribute('role', 'status');
  const label = document.createElement('span'); label.className = 'assistant-context-label'; label.textContent = 'AKTIVITAET';
  const activity = document.createElement('p'); activity.className = 'assistant-context-detail'; activity.textContent = 'Noch kein Auftrag gestartet.';
  const knowledge = document.createElement('a'); knowledge.href = '/wissen'; knowledge.textContent = 'Wissen oeffnen';
  context.append(title, stage, status, label, activity, knowledge); main.append(context);
  const names = { idle: 'Bereit', listening: 'Hoert zu', working: 'Arbeitet', speaking: 'Spricht', error: 'Gespraech unterbrochen' };
  const update = state => { if (names[state]) { context.dataset.state = state; status.textContent = names[state]; } };
  update(document.body.dataset.activity || 'idle');
  window.addEventListener('jarvis:activity', event => update(event.detail?.state));
  window.addEventListener('jarvis:status', event => { activity.textContent = event.detail?.message || 'Keine Statusmeldung.'; });
  let renderer, observer, frame, disposed = false, elapsed = 0;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  try {
    const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(42, 1, .1, 100);
    camera.position.set(0, 0, 5.6);
    renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true }); renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
    renderer.domElement.setAttribute('aria-label', 'JARVIS Core'); renderer.domElement.setAttribute('role', 'img'); stage.append(renderer.domElement);
    scene.add(new THREE.AmbientLight(0xffffff, 2)); const light = new THREE.DirectionalLight(0xffffff, 3); light.position.set(3, 5, 5); scene.add(light);
    const core = createCore(); scene.add(core.group);
    const render = () => {
      if (disposed) return;
      renderer.render(scene, camera); stage.dataset.frames = String(Number(stage.dataset.frames || 0) + 1);
    };
    const visible = () => !document.hidden && getComputedStyle(document.getElementById('chat-screen')).display !== 'none' && stage.clientWidth > 0;
    function animate(time) {
      frame = null;
      if (disposed || !visible()) return;
      const delta = Math.min((time - elapsed) / 1000 || 0, .05); elapsed = time;
      if (!reduced.matches) {
        core.rings.forEach((ring, i) => { ring.rotation.z += delta * (context.dataset.state === 'working' ? .6 : .12) * (i % 2 ? -1 : 1); });
        core.root.scale.setScalar(context.dataset.state === 'speaking' || context.dataset.state === 'listening' ? 1 + Math.sin(time / 200) * .06 : 1);
      }
      render(); if (!reduced.matches) frame = requestAnimationFrame(animate);
    }
    const resume = () => { if (frame) cancelAnimationFrame(frame); frame = null; elapsed = 0; if (visible()) frame = requestAnimationFrame(animate); };
    observer = new ResizeObserver(() => {
      if (!stage.clientWidth || !stage.clientHeight) return;
      renderer.setSize(stage.clientWidth, stage.clientHeight); camera.aspect = stage.clientWidth / stage.clientHeight; camera.updateProjectionMatrix(); resume();
    }); observer.observe(stage);
    const loginObserver = new MutationObserver(resume); loginObserver.observe(document.getElementById('chat-screen'), { attributes: true, attributeFilter: ['class'] });
    document.addEventListener('visibilitychange', resume); reduced.addEventListener('change', resume);
    window.addEventListener('jarvis:activity', resume);
    renderer.domElement.addEventListener('webglcontextlost', event => { event.preventDefault(); stage.dataset.error = 'true'; status.textContent = 'Visualisierung nicht verfuegbar'; });
    window.addEventListener('pagehide', () => {
      disposed = true; if (frame) cancelAnimationFrame(frame); observer.disconnect(); loginObserver.disconnect(); reduced.removeEventListener('change', resume);
      core.group.traverse(object => { object.geometry?.dispose(); object.material?.dispose(); }); renderer.dispose();
    }, { once: true });
  } catch (_) { stage.textContent = 'Visualisierung nicht verfuegbar'; stage.dataset.error = 'true'; }
}
