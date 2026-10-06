#!/usr/bin/env bash
# Sicheres, reproduzierbares Produktionsupdate fuer den Jarvis-Service.

set -Eeuo pipefail

EXPECTED_BRANCH="${JARVIS_UPDATE_BRANCH:-master}"
REMOTE="${JARVIS_UPDATE_REMOTE:-origin}"
SERVICE="jarvis"
HEALTH_TIMEOUT="${JARVIS_HEALTH_TIMEOUT:-120}"
HEALTH_INTERVAL="${JARVIS_HEALTH_INTERVAL:-3}"
COMPOSE_FILES=(-f docker-compose.yml)
DOCKER_CMD=()

if [[ -f docker-compose.override.yml ]]; then
  COMPOSE_FILES+=(-f docker-compose.override.yml)
fi

compose() {
  "${DOCKER_CMD[@]}" compose "${COMPOSE_FILES[@]}" "$@"
}

docker_cmd() {
  "${DOCKER_CMD[@]}" "$@"
}

fail() {
  printf 'Status: FAILED - %s\n' "$1" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Benoetigtes Kommando fehlt: $1"
}

select_docker_command() {
  if docker info >/dev/null 2>&1; then
    DOCKER_CMD=(docker)
    return
  fi
  if command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
    DOCKER_CMD=(sudo docker)
    return
  fi
  fail "Docker-Daemon ist nicht erreichbar"
}

env_mode() {
  if stat -c '%a' .env >/dev/null 2>&1; then
    stat -c '%a' .env
  else
    stat -f '%Lp' .env
  fi
}

printf '%s\n' 'JARVIS UPDATE'

[[ -d .git ]] || fail "Dieses Verzeichnis ist kein Git-Repository"
[[ -f docker-compose.yml ]] || fail "docker-compose.yml fehlt"
[[ -f Dockerfile ]] || fail "Dockerfile fehlt"
[[ -f .env ]] || fail ".env fehlt"

require_command git
require_command docker
require_command curl

current_branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
[[ "$current_branch" == "$EXPECTED_BRANCH" ]] || \
  fail "Falscher Branch: ${current_branch:-detached}; erwartet: $EXPECTED_BRANCH"

if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  fail "Arbeitsbaum ist nicht sauber. Serverkonfiguration gehoert in docker-compose.override.yml; tracked Dateien nicht automatisch stashen oder zuruecksetzen."
fi

mode="$(env_mode)"
mode_value=$((8#$mode))
(( (mode_value & 077) == 0 )) || \
  fail ".env-Dateirechte sind zu offen ($mode); einmalig ausfuehren: chmod 600 .env"

select_docker_command
docker_cmd info >/dev/null 2>&1 || fail "Docker-Daemon ist nicht erreichbar"
docker_cmd compose version >/dev/null 2>&1 || fail "Docker Compose ist nicht verfuegbar"

git remote get-url "$REMOTE" >/dev/null 2>&1 || fail "Git-Remote $REMOTE fehlt"
git fetch --quiet --prune "$REMOTE" "$EXPECTED_BRANCH" || fail "Git-Remote ist nicht erreichbar"

remote_ref="$REMOTE/$EXPECTED_BRANCH"
git merge-base --is-ancestor HEAD "$remote_ref" || \
  fail "Lokaler Stand ist nicht per Fast-Forward aktualisierbar"
git merge --ff-only "$remote_ref" || fail "Fast-Forward-Update fehlgeschlagen"

target_commit="$(git rev-parse HEAD)"
short_commit="$(git rev-parse --short=12 HEAD)"
export JARVIS_COMMIT="$target_commit"
printf '%s\n' 'Git: OK'

compose config --quiet || fail "Docker-Compose-Konfiguration ist ungueltig"
compose build "$SERVICE" || fail "Jarvis-Image konnte nicht gebaut werden"
printf '%s\n' 'Build: OK'

compose up -d --no-deps "$SERVICE" || fail "Jarvis-Container konnte nicht aktualisiert werden"

container_id="$(compose ps -q "$SERVICE")"
[[ -n "$container_id" ]] || fail "Jarvis-Container wurde nicht gefunden"
[[ "$(docker_cmd inspect -f '{{.State.Running}}' "$container_id" 2>/dev/null || true)" == "true" ]] || \
  fail "Jarvis-Container laeuft nicht"
printf '%s\n' 'Container: OK'

deadline=$((SECONDS + HEALTH_TIMEOUT))
backend_ok=0
while (( SECONDS < deadline )); do
  if compose exec -T "$SERVICE" \
      curl --fail --silent --show-error --insecure --max-time 5 \
      https://127.0.0.1/api/health >/dev/null 2>&1; then
    backend_ok=1
    break
  fi
  sleep "$HEALTH_INTERVAL"
done
(( backend_ok == 1 )) || fail "Backend-Health-Check nach ${HEALTH_TIMEOUT}s fehlgeschlagen"
printf '%s\n' 'Backend: OK'

running_commit="$(docker_cmd inspect -f '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$container_id" 2>/dev/null || true)"
[[ "$running_commit" == "$target_commit" ]] || \
  fail "Laufender Container entspricht nicht dem erwarteten Commit"

printf 'Commit: %s\n' "$short_commit"
printf '%s\n' 'Status: SUCCESS'
