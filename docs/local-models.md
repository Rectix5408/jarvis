# Jarvis mit einem lokalen Modell

Jarvis besitzt bereits den Provider `openai_compatible` in `backend/llm.py`.
Er unterstuetzt Ollama, LM Studio und andere kompatible Modellserver,
einschliesslich nativer Werkzeugaufrufe und optionalem Prompt-Tool-Calling.
Ein eigener Provider oder ein erfundener API-Key ist dafuer nicht notwendig.

## Auf dem Linux-Server

Die neue optionale Compose-Datei baut euren modifizierten Jarvis-Quellcode und
startet Ollama im selben Docker-Netz. Modellgewichte werden persistent im Volume
`ollama-models` gespeichert. Ollamas Port wird NICHT am Host veroeffentlicht;
Jarvis erreicht den Dienst intern. Ollamas Cloud-Funktionen sind abgeschaltet.
Die erste Installation und der Modelldownload benoetigen Internet.

Die vorhandene `.env` muss vorhanden sein und euer Server-IP, Login-Passwort und
HMAC-Geheimnis enthalten. Ein Provider-Key ist fuer dieses lokale Profil nicht
noetig. `SECRET_KEY` ist niemals ein OpenRouter-/Ollama-API-Key.

Im Repository-Ordner auf dem Server:

```bash
sudo docker compose -f docker-compose.yml -f docker-compose.local-llm.yml config --quiet
sudo docker compose -f docker-compose.yml -f docker-compose.local-llm.yml up -d --build
sudo docker compose -f docker-compose.yml -f docker-compose.local-llm.yml exec ollama ollama pull qwen3:4b
```

`qwen3:4b` ist ein vorlaeufiges kleines Testmodell mit Werkzeugunterstuetzung,
keine Zusage fuer optimale Agentenqualitaet oder Geschwindigkeit auf eurem
Server. Die endgueltige Auswahl haengt von RAM, GPU/VRAM, Kontext und den
benoetigten Werkzeugen ab. Keine Cloud-Modell-Tags verwenden.

Diese Befehle verwenden absichtlich explizite Compose-Dateien. Eine zuvor
angelegte `docker-compose.override.yml` wird dabei nicht automatisch geladen.
Wenn sie eigene Anpassungen enthaelt, muss sie bewusst als weitere `-f`-Datei
eingebunden werden; `docker-compose.local-llm.yml` kommt nach ihr.
Beim ersten Start nicht `--pull never` verwenden: Ollamas Image muss erst
heruntergeladen werden. Der Jarvis-Dienst hat `pull_policy: build` und verwendet
den lokalen Quellcode statt eines fremden fertigen Jarvis-Images.

In Jarvis unter Einstellungen -> KI & System ein Profil anlegen:

| Feld | Wert |
| --- | --- |
| Name | Lokal - Ollama |
| Provider | OpenAI-Kompatibel |
| API-URL | `http://ollama:11434/v1/chat/completions` |
| Modell | `qwen3:4b` (oder der exakt heruntergeladene Modellname) |
| API-Key | leer |
| Prompt-Tool-Calling | zunaechst aus; native Werkzeugaufrufe verwenden |

Profil speichern, Verbindung testen und als aktives Profil auswaehlen.
Zunaechst einen normalen Chat und danach einen aktivierten, erlaubten Skill
testen. Rollen mit einer expliziten anderen Profil-ID verwenden weiterhin jenes
Profil; fuer lokalen Betrieb auf das lokale Profil oder Vererbung umstellen.
Benutzerbezogene Profile und explizite Profilberechtigungen ebenfalls beachten.

Fuer den ersten Test: Antwort-Timeout 300 Sekunden, maximale Antwortlaenge
1024 Tokens. Der Container verwendet standardmaessig 16384 Kontext-Tokens.
Mehr Kontext verbraucht mehr Speicher. Fuer umfangreiche Agentenprompts/Skills
kann mehr Kontext erforderlich sein; bei einer Kontextfehlermeldung gezielt
anpassen statt unbegrenzt zu erhoehen. Optional in `.env`:

```dotenv
OLLAMA_CONTEXT_LENGTH=16384
OLLAMA_KEEP_ALIVE=10m
```

Diagnose:

```bash
sudo docker compose -f docker-compose.yml -f docker-compose.local-llm.yml ps
sudo docker compose -f docker-compose.yml -f docker-compose.local-llm.yml exec ollama ollama list
sudo docker compose -f docker-compose.yml -f docker-compose.local-llm.yml exec ollama ollama ps
sudo docker compose -f docker-compose.yml -f docker-compose.local-llm.yml logs --tail=80 ollama
```

`localhost:11434` im Jarvis-Container bezeichnet den Jarvis-Container selbst,
nicht den Ollama-Container und nicht euren Mac. Fuer diesen Aufbau lautet die
Adresse deshalb `ollama:11434`.

## Optionale NVIDIA-GPU

Nur auf einem Linux-Rechner mit verfuegbarer NVIDIA-GPU und installiertem
NVIDIA Container Toolkit:

```bash
sudo docker compose -f docker-compose.yml -f docker-compose.local-llm.yml -f docker-compose.local-llm.nvidia.yml up -d --build
```

Die Basiskonfiguration benoetigt keine GPU und kann auf CPU laufen. Wie schnell
sie ist, muss auf eurer Hardware gemessen werden. Die GPU-Datei ist nicht fuer
Mac-GPUs oder AMD gedacht.

## Modell auf einem anderen Rechner / Mac

Auf einem Apple-Silicon-Mac Ollama vorzugsweise nativ betreiben, damit die GPU
genutzt werden kann; Docker Desktop auf macOS bietet Ollama kein GPU-Passthrough.
Laeuft Jarvis in Docker Desktop auf demselben Mac, lautet die Profil-URL fuer
den nativen Dienst `http://host.docker.internal:11434/v1/chat/completions`.
Erreichbarkeit und Bind-Adresse des nativen Dienstes gesondert pruefen.

Laeuft Jarvis auf dem Cloud-Server und das Modell auf einem separaten Rechner,
braucht der Server eine tatsaechlich erreichbare private Netzwerk-/VPN-Adresse
dieses Rechners. `host.docker.internal` verbindet nicht mit einem entfernten Mac.
Die Server-Compose-Datei ist dann nicht der richtige Modell-Standort; das
bestehende Profil kann stattdessen auf den privaten Modellserver zeigen.

## Einordnung der drei Referenzvideos

Die untersuchten Einzelbilder zeigen ein animiertes Wissensnetz, ein
Arbeits-Dashboard und eine futuristische Sprachoberflaeche. Sie belegen nicht,
welche internen Dienste, Modelle oder Integrationen die gezeigten Systeme
verwenden. Die Clips wurden visuell anhand von zwoelf Stichproben pro Video
analysiert; keine Behauptung ueber vollstaendig transkribierte Audios.

In diesem Repository sind bereits Agenten/Skills, Wissenssuche, Aufgaben,
Browsersteuerung, WhatsApp, Mikrofon-Eingabe und Sprachausgabe vorhanden.
Die lokalen Modelle koennen diese ueber die bestehenden Berechtigungen nutzen,
sofern das Modell die jeweiligen Aufgaben und Werkzeugaufrufe beherrscht.
Unter `/dashboard` ist eine eigene, von den Referenzen inspirierte Ansicht
eingebaut; der Einstieg steht im Portal. Das animierte Three.js-Wissensnetz
zeigt freigegebene Dateien und ihre Gruppenzuordnungen, keine behaupteten
semantischen Aehnlichkeiten. Es verwendet `/api/wissen/files` mit dessen
bestehendem Benutzer-Scope (aktuell editierbare Gruppen). Die Dateiliste bleibt
vollstaendig; nur die 3D-Geometrie zeigt maximal 350 Suchtreffer, um den Browser
nicht zu ueberlasten. Gruppen ohne zugeordnete Dateien werden nicht gezaehlt.

Modellwahl, Erreichbarkeit und CPU stammen ebenfalls aus bestehenden APIs.
Das Dashboard startet den bestehenden Chat mit Mikrofon als Dialog; ein Klick
auf das Ausklapp-Symbol oeffnet ihn separat. Spracheingabe benoetigt HTTPS oder
localhost und die Browserfreigabe. Authentifizierung und Profil-/Werkzeugrechte
bleiben serverseitig unveraendert. Bei fehlender Anmeldung werden keine
Demo-Systemdaten angezeigt. Three.js und Lucide sind lokal mitgeliefert und
benoetigen keinen CDN-Zugriff.

Lokales LLM bedeutet nicht automatisch komplett offline: der Backend-TTS-Pfad
in `backend/main.py` nutzt derzeit `edge-tts`, und manche Browser-Spracherkenner
sowie externe Skills brauchen Netz. Fuer komplett lokale Sprache waeren lokale
STT und TTS gesondert einzurichten. Whisper-STT ist teilweise bereits vorhanden.

## Verifikation und Quellen

Dashboard-Tests (benoetigen `fastapi`, `httpx`, Node.js und Chromium):

```bash
python tests/test_dashboard_route.py
npm install --prefix /tmp/jarvis-dashboard-test playwright pngjs
PLAYWRIGHT_BROWSERS_PATH=/tmp/jarvis-playwright-browsers /tmp/jarvis-dashboard-test/node_modules/.bin/playwright install chromium
python tests/preview_dashboard.py --port 8766
```

Im zweiten Terminal:

```bash
PLAYWRIGHT_BROWSERS_PATH=/tmp/jarvis-playwright-browsers DASHBOARD_NODE_MODULES=/tmp/jarvis-dashboard-test/node_modules node tests/test_dashboard_ui.cjs
```

Die Vorschau unter `http://127.0.0.1:8766/dashboard` liefert ausschliesslich
statische Dateien, keine echte Anmeldung und keine echten Systemdaten. Die
Browser-Tests mocken API-Antworten nur im Test und pruefen Desktop/Mobil,
Canvas-Pixel, Bewegung/Pause, Auswahl, Dateisuche, Profile inklusive abgewiesener
Aktivierung, 401/403-Datenbereinigung, HTML-Injection und den Chat-Einstieg.
Ein echter Modellaufruf oder eine Mikrofonaufnahme wird dadurch nicht getestet.
In der installierten Anwendung zuerst wie gewohnt anmelden, dann `/dashboard`
oeffnen. Bestehender Chat, Backend-Sicherheitsregeln und Provider bleiben erhalten.

Die Compose-Dateien sind vorbereitete Konfiguration; auf diesem Mac ist kein
Docker installiert. Ein echter Containerstart/Modelldownload und ein Test auf
eurem Server stehen daher aus. Das aktive Modellprofil und reale Serverdaten
wurden nicht veraendert.

Primaerquellen:

* https://docs.ollama.com/docker
* https://docs.ollama.com/api/openai-compatibility
* https://docs.ollama.com/faq (Cloud deaktivieren, Kontext, macOS GPU)
* https://ollama.com/library/qwen3:4b
