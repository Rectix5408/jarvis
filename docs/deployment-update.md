# Jarvis Production Update

`update.sh` aktualisiert ausschließlich den Compose-Service `jarvis`. Es baut
das Image aus dem ausgecheckten Commit, startet den Service mit `--no-deps`,
wartet auf `/api/health` und verifiziert anschließend das Commit-Label.

## Einmalige Migration

Die produktiven Portbindungen gehören in die unversionierte Datei
`docker-compose.override.yml`. Eine Vorlage liegt als
`docker-compose.override.yml.example` vor. Eine vorhandene Override-Datei nicht
überschreiben; stattdessen sicherstellen, dass ihre `ports`-Liste `!override`
verwendet und auf `127.0.0.1:8080:80` sowie `127.0.0.1:8088:443` zeigt.

Falls `docker-compose.yml` auf dem Server noch lokale Portänderungen enthält:

```bash
git diff -- docker-compose.yml > "$HOME/jarvis-compose-local.patch"
cp -n docker-compose.override.yml.example docker-compose.override.yml
git restore docker-compose.yml
chmod 600 .env
```

Den Patch nur als Migrationsnachweis aufbewahren. Vor dem Update mit
`docker compose -f docker-compose.yml -f docker-compose.override.yml config`
prüfen, dass die effektiven Portbindungen stimmen. Danach:

```bash
./update.sh
```

Das Skript überschreibt weder `.env` noch `data/`, OAuth-Dateien oder benannte
Volumes. Ein unsauberer Git-Arbeitsbaum, unsichere `.env`-Rechte, ein nicht per
Fast-Forward erreichbarer Stand oder ein fehlgeschlagenes Health Gate führen zu
einem Abbruch. Ollama wird weder gebaut noch gestartet oder neu geladen.
