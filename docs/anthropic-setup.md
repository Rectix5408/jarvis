# Anthropic in Jarvis konfigurieren

## API-Guthaben und Schluessel

Das Guthaben muss im API-/Console-Konto liegen. Ein Claude-Chat-Abonnement oder
Extra-Usage-Guthaben ist nicht automatisch dasselbe wie Console-API-Guthaben.
Prueft den Betrag im Billing-Bereich der Claude Console. Einen offengelegten Key
widerrufen und unter Settings -> API keys einen neuen anlegen.

API-Keys nicht in `.env.example`, Git, Screenshots oder Chatnachrichten ablegen.
Fuer die Einrichtung ueber die Website braucht ihr `.env` nicht zu bearbeiten.
Nur die bereits berechtigten Administratoren duerfen Profile/Keys bearbeiten.
Die Website fuer sensible Eingaben ueber HTTPS aufrufen.

## Einrichtung auf der Website

1. Als Administrator anmelden.
2. Einstellungen -> KI & System -> LLM-Profile -> Neues Profil.
3. Name: `Anthropic - Haiku`.
4. Provider: `Anthropic Claude` (`anthropic`), Anmeldung per API-Key, nicht Session-Cookie.
5. URL: `https://api.anthropic.com/v1/messages`.
6. Modell: `claude-haiku-4-5-20251001` oder ein verfuegbares Modell aus der API-Modellliste.
7. Neuen API-Key ins Passwortfeld eingeben.
8. Verbindung testen. Das fragt die Modellliste vom Jarvis-Backend aus ab.
9. Optional: Anthropic-Antwort testen und die Kostenbestaetigung akzeptieren.
10. Speichern, anschliessend das Profil in der Liste aktivieren. Falls im Chat
    eine benutzerbezogene Profilwahl gesetzt ist, dort ebenfalls dieses Profil waehlen.

Der neue Antworttest sendet eine feste kurze Nachricht an die direkte Anthropic
Messages API mit maximal 32 Ausgabetokens, ohne Denkbudget, Werkzeuge, RAG-Daten
oder Agentenschleife. Er prueft eine echte Generierung, keine beliebigen Aufgaben.
Er kann API-Kosten verursachen und wird nie automatisch beim Laden einer Seite
oder durch Status-Polling ausgeloest. Er ist auf direkte API-Key-Profile beschraenkt.
Eine Antwort bedeutet nicht, dass jede Agentenaufgabe oder jedes Tool bereits
geprueft wurde. Fehlermeldungen zeigen keine Provider-Antwortkoerper oder Keys.

Weitere Anbieter und lokale Modellserver koennen weiterhin ueber denselben
Profil-Editor konfiguriert und mit dem bestehenden Verbindungstest geprueft
werden. OpenAI-kompatible Chat-URLs werden fuer Modellabfragen jetzt korrekt
auf `/models` abgebildet.

## Verifikation

```bash
python tests/test_profile_response.py
JSDOM_PATH=/tmp/jarvis-ui-test-tools/node_modules/jsdom node tests/test_profile_response_ui.cjs
python tests/test_license.py
```

Diese Tests verwenden ausschliesslich simulierte HTTP-Antworten und Dummy-Keys.
Sie verbrauchen kein Guthaben. Fuer einen echten Test auf dem Cloud-Server muessen
die Aenderungen erst deployed werden und ein neuer legitimer Key im Profil liegen.
Ein ausstehender SSH-Zugang ist kein erfolgreich ausgefuehrter Servertest.

Offizielle Referenzen:
- https://platform.claude.com/docs/en/models/haiku-4-5/overview
- https://platform.claude.com/docs/en/api/messages/create
- https://platform.claude.com/docs/en/manage-claude/authentication
- https://support.claude.com/en/articles/8977456-how-do-i-pay-for-my-claude-api-usage
