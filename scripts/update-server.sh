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

# Preserve every production override, including scheduler activation.
mapfile -t metadata < <(docker inspect stock-briefing-api-1 --format '{{json .Config.Labels}}' | python3 -c '
import json,sys
labels=json.load(sys.stdin)
print(labels["com.docker.compose.project"])
print(labels["com.docker.compose.project.working_dir"])
print(labels["com.docker.compose.project.config_files"])
')
[[ ${#metadata[@]} == 3 && "${metadata[1]}" == "$PWD" ]] || { echo 'Deployment metadata mismatch'; exit 1; }
compose=(docker compose --project-name "${metadata[0]}" --project-directory "$PWD")
IFS=',' read -ra files <<< "${metadata[2]}"
for file in "${files[@]}"; do compose+=(--file "$file"); done
"${compose[@]}" build api telegram
"${compose[@]}" up -d db api telegram
"${compose[@]}" ps
"${compose[@]}" exec -T api python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8000/healthz").read().decode())'
printf '\n업데이트 완료. 텔레그램에서 버전을 입력하세요.\n'
