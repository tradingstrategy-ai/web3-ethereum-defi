#!/usr/bin/env bash
# Host-side daemon preflight, including older scanners without a lifecycle guard.
set -euo pipefail

if [[ $# != 1 || ( "$1" != list && "$1" != run ) ]]; then
    echo "Usage: $0 list|run (DRY_RUN=true by default)" >&2
    exit 2
fi
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ "$1" == run ]]; then
    # -a includes stopped, paused and restarting containers. Query all instances,
    # not only the conventional container name, and fail closed on Docker errors.
    containers=$(docker compose -f docker-compose.yml ps --all --quiet vault-scanner-looped)
    # Also find the fixed production name from an older checkout/project whose
    # image may predate the lifecycle guard. Use the same base Compose file as run.
    named_containers=$(docker ps --all --quiet --filter 'name=^/?vault-scanner-looped$')
    for container in $containers $named_containers; do
        status=$(docker inspect --format '{{.State.Status}}' "$container")
        case "$status" in
            created|exited) ;;
            *) echo "Migration blocked: vault-scanner-looped container $container is $status. Stop the daemon before retrying." >&2; exit 1 ;;
        esac
    done
fi

# Pin the one-shot to the inspected content ID rather than a changing local tag.
image_id=$(docker image inspect vault-scanner:local --format '{{.Id}}')
if [[ ! "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "Cannot establish scanner image provenance" >&2
    exit 1
fi
override=$(mktemp)
trap 'rm -f "$override"' EXIT
printf 'services:\n  vault-scanner-oneshot:\n    image: "%s"\n' "$image_id" > "$override"
docker compose -f docker-compose.yml -f "$override" run --rm --no-deps \
    -e "DRY_RUN=${DRY_RUN:-true}" \
    -e "LOG_LEVEL=${LOG_LEVEL:-info}" \
    -e SCAN_PRICES=false -e UPSHIFT_SCAN_PRICES=false \
    --entrypoint vault-migrate vault-scanner-oneshot "$1"
