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

# Include the scheduled worker so all services use the same build identity.
docker compose build api telegram worker
docker compose up -d api telegram
docker compose ps
curl -fsS http://127.0.0.1:8000/healthz
printf '\n업데이트 완료. 텔레그램에서 버전을 입력하세요.\n'
