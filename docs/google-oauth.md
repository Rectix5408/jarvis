# Google OAuth

Jarvis verwendet für die normale Gmail-, Drive- und Calendar-Verbindung einen
OAuth-2.0-Authorization-Code-Web-Flow. Der separate GOG/OpenClaw-Desktop-Flow
mit einem localhost-Redirect bleibt davon unabhängig.

## Konfiguration

Erstelle in Google Cloud einen OAuth-Client vom Typ **Web application** und
aktiviere Gmail API, Drive API und Calendar API. Konfiguriere serverseitig:

```env
GOOGLE_OAUTH_CLIENT_ID=...
GOOGLE_OAUTH_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=https://jarvis.example.com/api/google/callback
```

Die Redirect-URI muss in Google Cloud exakt übereinstimmen. Für die produktive
Installation ist dies `https://jarvis.virus-event.de/api/google/callback`.

## Sicherheit und Speicherung

Jarvis fordert derzeit `openid`, `userinfo.email`, `gmail.modify`, `drive` und
`calendar` an, damit bestehende Schreibfunktionen kompatibel bleiben. Ein
Least-Privilege-Audit ist als separate Folgearbeit vorgesehen.

OAuth-State wird serverseitig in `data/google_auth/oauth_states.sqlite3`
gespeichert, an den startenden Administrator gebunden, nach zehn Minuten
ungültig und nur einmal verwendbar. Tokens liegen ausschließlich serverseitig
in `data/google_auth/token.json`. Die Datei wird atomisch geschrieben und unter
Unix mit Modus `0600` geschützt. Vorhandene Refresh-Tokens bleiben erhalten,
wenn Google bei einer erneuten Autorisierung oder Aktualisierung keines liefert.
