#!/usr/bin/env bash
# Run once on the Ubuntu host, after reviewing POSTGRES_MIGRATION.md.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ -e compose.scheduler.yaml ]]; then
    echo 'Scheduler override already exists; inspect migration state before proceeding.'
    exit 1
fi

# Restore deployment paths without printing environment values.
source scripts/compose-context.sh
load_compose_context "$@"
[[ "$deployment_backend" == sqlite ]] || { echo 'Migration requires the old SQLite API container'; exit 1; }
[[ -f "${deployment[3]}" ]] || { echo 'SQLite database missing'; exit 1; }
validate_compose_context

# Confirm the effective backend is PostgreSQL, honoring all managed overrides.
configuration=$("${compose[@]}" config --format json)
secret_path=$(printf '%s' "$configuration" | python3 -c '
import json,sys
c=json.load(sys.stdin)
for name in ("api",):
    if c["services"][name]["environment"].get("STOCK_DATABASE_BACKEND") != "postgres":
        raise SystemExit("An override is masking the PostgreSQL backend")
for v in c["services"]["db"]["volumes"]:
    if v["target"] == "/run/secrets/postgres_password":
        print(v["source"]); break
else: raise SystemExit("Password mount missing")
')
unset configuration
[[ -n "$secret_path" ]] || exit 1
mkdir -p "$(dirname "$secret_path")"
if [[ ! -e "$secret_path" ]]; then
    (umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > "$secret_path")
fi
[[ -f "$secret_path" && -s "$secret_path" ]] || { echo 'Password must be a nonempty file'; exit 1; }

# Build before the maintenance window. This does not start a scheduler or send.
export BUILD_TIME BUILD_COMMIT
BUILD_TIME=$(date -u '+%Y-%m-%dT%H:%M:%S+00:00')
BUILD_COMMIT=$(git rev-parse HEAD)
"${compose[@]}" build api
"${compose[@]}" up -d db
for attempt in {1..60}; do
    if "${compose[@]}" exec -T db pg_isready -U stock_briefing -d stock_briefing >/dev/null 2>&1; then break; fi
    sleep 2
done
"${compose[@]}" exec -T db pg_isready -U stock_briefing -d stock_briefing >/dev/null

# Never interrupt a briefing currently in progress.
sudo systemctl disable --now stock-briefing.timer
if systemctl is-active --quiet stock-briefing.service; then
    echo 'Daily briefing is running. Wait for it to finish, then rerun this script.'
    exit 1
fi
# An old standalone Telegram container must already be stopped before migration.
if docker ps --filter "label=com.docker.compose.project=${deployment[0]}" --filter 'label=com.docker.compose.service=telegram' --format '{{.ID}}' | grep -q .; then
    echo 'Stop the old standalone Telegram container before continuing.' >&2
    exit 1
fi
"${compose[@]}" stop api

backup="$(dirname "${deployment[3]}")/stock-before-postgres-$(date +%Y%m%d-%H%M%S).db"
python3 - "${deployment[3]}" "$backup" <<'PY'
from pathlib import Path
import os, sqlite3, sys
source, target = map(Path, sys.argv[1:])
fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
os.close(fd)
with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
print('Consistent SQLite backup:', target)
PY

"${compose[@]}" run --rm --no-deps --entrypoint python api \
  -m app.migrate_database --sqlite "$(python3 - "${deployment[4]}" "$backup" <<'PYTHON'
from pathlib import PurePosixPath
import sys
print(PurePosixPath(sys.argv[1]).parent / PurePosixPath(sys.argv[2]).name)
PYTHON
)"

# Set scheduler activation as the last override, overriding old managed values.
activation="$PWD/compose.scheduler.yaml"
if [[ -e "$activation" ]]; then
    echo 'compose.scheduler.yaml already exists; inspect it before starting services.'
    exit 1
fi
cat > "$activation" <<'YAML'
services:
  api:
    environment:
      DAILY_SCHEDULER_ENABLED: "1"
YAML
"${compose[@]}" --file "$activation" up -d --no-build --remove-orphans api
echo 'Migration verified. API daily scheduler enabled at 08:30 Asia/Seoul.'
echo 'Use the complete Compose file list (including compose.scheduler.yaml) for future updates.'
echo 'After 08:30, a not-yet-sent briefing may be generated and sent on API startup.'
