/* UI-Test: Laufzustand wird freigegeben, wenn kein 'finished' mehr kommt.
 *
 * Geprueft wird gegen die ECHTEN Dateien (frontend/chat.html + js/chat.js,
 * chatlib.js, i18n.js). Der WebSocket ist eine Attrappe – damit laesst sich
 * genau der Fall herstellen, der in echt nur bei Netzstoerungen auftritt:
 * der Lauf startet, dann bricht die Verbindung ab.
 *
 * DER FEHLER, den das absichert: agentRunning wurde NUR von den WS-Ereignissen
 * started/finished gesetzt. Blieb 'finished' aus, war die Oberflaeche dauerhaft
 * blockiert – Senden UND Wiederholen liefen in "Bitte stoppe zuerst die
 * laufende Aufgabe", und der Stop-Knopf half nicht, weil seine Bestaetigung
 * ueber denselben toten Socket gekommen waere. Nur Neuladen half.
 *
 * Lauf:  timeout 60 node tests/test_chat_release_ui.js
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { JSDOM } = require(process.env.JSDOM_PATH || '/tmp/node_modules/jsdom');

const ROOT = path.resolve(__dirname, '..');
const results = [];
function check(name, cond, detail) {
    results.push({ name, ok: !!cond, detail: detail || '' });
    console.log((cond ? '  \x1b[32m✓\x1b[0m ' : '  \x1b[31m✗\x1b[0m ') + name
        + (!cond && detail ? ' – ' + detail : ''));
}
function section(t) { console.log('\n\x1b[1m' + t + '\x1b[0m'); }

const HTML = fs.readFileSync(path.join(ROOT, 'frontend/chat.html'), 'utf8');
const I18N = fs.readFileSync(path.join(ROOT, 'frontend/js/i18n.js'), 'utf8');
const CHATLIB = fs.readFileSync(path.join(ROOT, 'frontend/js/chatlib.js'), 'utf8');
const CHAT = fs.readFileSync(path.join(ROOT, 'frontend/js/chat.js'), 'utf8');

/* Fenster mit Attrappen aufbauen. Wichtig: WebSocket und fetch MUESSEN vor
 * dem eval von chat.js stehen – chat.js verbindet sich beim Start. */
function fenster({ voice = false } = {}) {
    const dom = new JSDOM(HTML, { url: 'https://localhost/chat', runScripts: 'outside-only' });
    const w = dom.window;
    const util = require('util');
    if (typeof w.TextDecoder === 'undefined') w.TextDecoder = util.TextDecoder;
    if (typeof w.TextEncoder === 'undefined') w.TextEncoder = util.TextEncoder;
    // jsdom kennt matchMedia nicht (chat.js prueft damit auf Touch-Geraete)
    if (typeof w.matchMedia === 'undefined') {
        w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
    }
    if (typeof w.scrollTo === 'undefined') w.scrollTo = () => {};
    // rAF selbst stellen, NICHT per pretendToBeVisual: das startet einen
    // Dauerlauf, der den Node-Prozess am Ende offen haelt (bekannter
    // Fallstrick der jsdom-Tests in diesem Projekt).
    if (typeof w.requestAnimationFrame === 'undefined') {
        w.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 0);
        w.cancelAnimationFrame = (id) => clearTimeout(id);
    }

    const sockets = [];
    function FakeWS(url) {
        this.url = url;
        this.readyState = 1;            // OPEN – chat.js sendet sonst nichts
        this.gesendet = [];
        sockets.push(this);
        FakeWS.letzter = this;
    }
    FakeWS.prototype.send = function (s) { this.gesendet.push(s); };
    FakeWS.prototype.close = function () { this.readyState = 3; };
    w.WebSocket = FakeWS;
    w.sockets = sockets;
    w.__recognitions = []; w.__audios = [];
    if (voice) {
        w.SpeechRecognition = class {
            constructor() { w.__recognitions.push(this); }
            start() { this.onstart?.(); }
            stop() { this.onend?.(); }
            abort() { this.aborted = true; }
        };
        w.Audio = class {
            constructor(src) { this.src = src; w.__audios.push(this); }
            async play() { this.onplaying?.(); }
            pause() { this.paused = true; }
        };
        w.URL.createObjectURL = () => 'blob:fixture';
        w.URL.revokeObjectURL = () => {};
        w.confirm = () => true;
    }

    // Alle Netzaufrufe der Seite beantworten (Profile, Status, Sitzungen …).
    w.fetch = function (url) {
        const u = String(url);
        let body = {};
        if (/\/api\/chat\/sessions/.test(u)) body = { sessions: [{ id: 's1', title: 'Test' }] };
        if (/\/api\/me/.test(u)) body = { username: 'jarvis', is_admin: true, permissions: {} };
        return Promise.resolve({
            ok: true, status: 200,
            json: () => Promise.resolve(body),
            text: () => Promise.resolve(JSON.stringify(body)),
            blob: () => Promise.resolve(new w.Blob(['fixture'])),
        });
    };
    // Angemeldet starten, sonst haengt chat.js im Login-Bildschirm.
    w.localStorage.setItem('jarvis_token', 'testtoken');
    w.localStorage.setItem('jarvis_chat_token', 'testtoken');
    w.alert = function (m) { w.__alert = m; };

    w.eval(I18N);
    w.eval(CHATLIB);
    w.eval(fs.readFileSync(path.join(ROOT, 'frontend/js/audio_output.js'), 'utf8'));
    w.eval(fs.readFileSync(path.join(ROOT, 'frontend/js/conversation.js'), 'utf8'));
    w.eval(CHAT);
    w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
    return { dom, w, FakeWS };
}

function statusZeilen(w) {
    return Array.from(w.document.querySelectorAll('.msg-status')).map((e) => e.textContent);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {

section('1. Aufbau');
let u;
try {
    u = fenster();
    // chat.js verbindet erst NACH der Anmelde-Pruefung (fetch /api/me) – ohne
    // diese Wartezeit gibt es noch keinen Socket, und der Test wuerde einen
    // Fehler melden, den es nicht gibt.
    await sleep(120);
    check('chat.js laeuft in jsdom an', !!u.w.document.getElementById('messages'));
    check('WebSocket-Attrappe wurde benutzt', u.w.sockets.length > 0,
        `${u.w.sockets.length} Sockets`);
} catch (e) {
    check('chat.js laeuft in jsdom an', false, e.message);
}

if (u && u.w.sockets.length) {
    const w = u.w;
    const ws = u.FakeWS.letzter;
    const stopBtn = w.document.getElementById('btn-stop');

    section('2. Lauf startet');
    // 'started' wie das Backend es sendet
    ws.onmessage({ data: JSON.stringify({
        type: 'agent_event', event: 'started',
        agent: { agent_id: 'a1', label: 'Jarvis', is_sub_agent: false }, agents: [] }) });
    check('Stop-Knopf ist sichtbar (Lauf laeuft)',
        stopBtn && !stopBtn.classList.contains('hidden'));
    check('Cockpit meldet den echten Agent-Start', w.document.body.dataset.activity === 'working');
    const vorZeilen = statusZeilen(w).length;

    section('3. Verbindungsabbruch OHNE finished');
    ws.onclose();
    check('Cockpit meldet einen verlorenen Lauf als Fehler', w.document.body.dataset.activity === 'error');
    check('Stop-Knopf wieder versteckt', stopBtn && stopBtn.classList.contains('hidden'));
    const zeilen = statusZeilen(w);
    check('es erscheint ein Hinweis', zeilen.length > vorZeilen,
        `${vorZeilen} -> ${zeilen.length}`);
    const letzte = zeilen[zeilen.length - 1] || '';
    check('der Hinweis nennt die Ursache (Verbindung)', /erbindung/.test(letzte), letzte);
    check('der Hinweis nennt den Weg zur Wiederholung (↻)', /↻/.test(letzte), letzte);

    section('4. Die Oberflaeche ist wieder benutzbar');
    // Senden darf NICHT mehr am "laeuft noch"-Riegel scheitern.
    w.__alert = null;
    const input = w.document.getElementById('msg-input');
    input.value = 'Zweite Frage';
    const form = w.document.getElementById('chat-form') || w.document.getElementById('msg-form');
    if (form) {
        form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
    } else {
        const btn = w.document.getElementById('btn-send');
        if (btn) btn.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
    }
    check('Senden ist nicht mehr blockiert',
        !(w.__alert && /stoppe/i.test(w.__alert)), String(w.__alert));

    section('5. Idempotenz');
    const vorher = statusZeilen(w).length;
    ws.onclose();
    ws.onclose();
    check('ein weiterer Abbruch im Ruhezustand meldet NICHTS',
        statusZeilen(w).length === vorher,
        `${vorher} -> ${statusZeilen(w).length}`);

    section('6. Normalfall bleibt unberuehrt');
    const u2 = fenster();
    await sleep(120);
    const ws2 = u2.FakeWS.letzter;
    const stop2 = u2.w.document.getElementById('btn-stop');
    ws2.onmessage({ data: JSON.stringify({
        type: 'agent_event', event: 'started',
        agent: { agent_id: 'a1', label: 'Jarvis', is_sub_agent: false }, agents: [] }) });
    ws2.onmessage({ data: JSON.stringify({
        type: 'status', message: 'Die Antwort.', highlight: true }) });
    ws2.onmessage({ data: JSON.stringify({
        type: 'agent_event', event: 'finished',
        agent: { agent_id: 'a1', label: 'Jarvis', is_sub_agent: false }, agents: [] }) });
    check('nach regulaerem finished: Stop-Knopf versteckt',
        stop2 && stop2.classList.contains('hidden'));
    check('Cockpit kehrt nach finished in den Ruhezustand zurueck', u2.w.document.body.dataset.activity === 'idle');
    check('nach regulaerem finished KEIN Verlust-Hinweis',
        !statusZeilen(u2.w).some((t) => /erbindung unterbrochen|keine Rückmeldung/.test(t)),
        statusZeilen(u2.w).join(' | ').slice(0, 120));
    // Ein Abbruch NACH dem Lauf darf ebenfalls nichts melden.
    ws2.onclose();
    check('Abbruch nach Lauf-Ende meldet nichts',
        !statusZeilen(u2.w).some((t) => /erbindung unterbrochen/.test(t)));
    u2.dom.window.close();

    section('7. Sprachgespraech im echten Chat-Modul');
    const v = fenster({ voice: true });
    await sleep(120);
    const voiceSocket = v.FakeWS.letzter;
    const toggle = v.w.document.getElementById('conversation-mode');
    toggle.checked = true; toggle.dispatchEvent(new v.w.Event('change'));
    check('Gespraech startet mit Mikrofon', v.w.document.body.dataset.activity === 'listening');
    v.w.__recognitions.at(-1).onresult({ results: [[{ transcript: 'Was steht heute an?' }]] });
    await sleep(50);
    check('Erkannter Satz wird als echter WS-Auftrag gesendet', voiceSocket.gesendet.some(raw => JSON.parse(raw).text === 'Was steht heute an?'));
    const event = (type, data) => voiceSocket.onmessage({ data: JSON.stringify({ type, ...data }) });
    const agent = { agent_id: 'a1', is_sub_agent: false };
    event('agent_event', { event: 'started', agent });
    event('status', { message: 'Ein Termin.', highlight: true });
    event('agent_event', { event: 'finished', agent });
    await sleep(30);
    check('Antwort wird ohne separaten TTS-Schalter gesprochen', v.w.__audios.length === 1 && v.w.document.body.dataset.activity === 'speaking');
    v.w.__audios[0].onended();
    check('Nach Audio-Ende hoert der Chat wieder zu', v.w.document.body.dataset.activity === 'listening');
    event('agent_event', { event: 'started', agent });
    v.w.document.getElementById('btn-mic').click();
    check('Mikrofon-Unterbrechung sendet Stop ans Backend', voiceSocket.gesendet.some(raw => JSON.parse(raw).action === 'stop'));
    const taskCount = () => voiceSocket.gesendet.filter(raw => JSON.parse(raw).type === 'task').length;
    const before = taskCount();
    v.w.__recognitions.at(-1).onresult({ results: [[{ transcript: 'Pruefe nur heute' }]] });
    await sleep(20);
    check('Neue Anweisung wartet auf Lauf-Ende', taskCount() === before);
    event('agent_event', { event: 'finished', agent });
    await sleep(50);
    check('Neue Anweisung folgt erst nach Backend-Bestaetigung', taskCount() === before + 1);
    voiceSocket.onclose();
    check('Verbindungsverlust schaltet Gespraech aus', !toggle.checked);
    v.dom.window.close();
}

const ok = results.filter((r) => r.ok).length;
const bad = results.length - ok;

console.log(`\n${'='.repeat(60)}\nErgebnis: ${ok}/${results.length} Pruefungen bestanden`);
if (bad) {
    console.log(`\x1b[31mFEHLGESCHLAGEN: ${bad}\x1b[0m`);
    results.filter((r) => !r.ok).forEach((r) =>
        console.log('  ✗ ' + r.name + (r.detail ? ' – ' + r.detail : '')));
}
// jsdom-Tests beenden sich nicht von selbst (Poll-Timer der Seite laufen weiter).
try { if (u) u.dom.window.close(); } catch (e) { /* egal */ }
process.exit(bad ? 1 : 0);

}   // ── Ende main()

main().catch((e) => { console.error('Testlauf abgebrochen:', e); process.exit(1); });
