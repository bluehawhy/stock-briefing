# Shared by the offline migration and normal updater. Caller uses set -euo pipefail.
load_compose_context() {
    local metadata
    metadata=$(docker inspect stock-briefing-api-1 | python3 scripts/compose-context.py read "$PWD" "$@") || return 1
    mapfile -d '' -t compose < <(printf '%s' "$metadata" | python3 -c 'import json,sys;sys.stdout.write("\0".join(json.load(sys.stdin)["arguments"])+"\0")')
    mapfile -t deployment < <(printf '%s' "$metadata" | python3 -c 'import json,sys;print("\n".join(json.load(sys.stdin)["deployment"]))')
    secret_directory=$(printf '%s' "$metadata" | python3 -c 'import json,sys;print(json.load(sys.stdin)["secret_directory"])')
    deployment_backend=$(printf '%s' "$metadata" | python3 -c 'import json,sys;print(json.load(sys.stdin)["backend"])')
}

validate_compose_context() {
    local configuration
    configuration=$("${compose[@]}" config --format json) || return 1
    printf '%s' "$configuration" | python3 scripts/compose-context.py validate "$secret_directory"
}
