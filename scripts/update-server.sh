#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ "$(git branch --show-current)" != "main" ]]; then
  echo "main 브랜치에서 실행하세요: git switch main" >&2
  exit 1
fi
if ! git diff --quiet HEAD --; then
  echo "커밋되지 않은 변경이 있습니다. 변경을 정리한 뒤 다시 업데이트하세요." >&2
  exit 1
fi

git pull --ff-only origin main
export BUILD_COMMIT
export BUILD_TIME
BUILD_COMMIT="$(git rev-parse HEAD)"
BUILD_TIME="$(date -u '+%Y-%m-%dT%H:%M:%S+00:00')"

# Preserve Compose and environment-file order; validate before any build/recreate.
source scripts/compose-context.sh
load_compose_context "$@"
validate_compose_context
"${compose[@]}" build api
# Require the old poller to be stopped before enabling the integrated one.
if docker ps --filter "label=com.docker.compose.project=${deployment[0]}" --filter 'label=com.docker.compose.service=telegram' --format '{{.ID}}' | grep -q .; then
  echo 'Stop the old standalone Telegram container before updating.' >&2
  exit 1
fi
"${compose[@]}" up -d --remove-orphans db api
"${compose[@]}" ps
"${compose[@]}" exec -T api python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8000/healthz").read().decode())'
printf '\n업데이트 완료. 텔레그램에서 버전을 입력하세요.\n'
