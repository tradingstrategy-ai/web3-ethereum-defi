#!/usr/bin/env bash
#
# Build the vault scanner Docker image with a git version stamp.
#
# Always use this script instead of calling ``docker compose build`` directly.
# It extracts the current git revision and passes it to Docker Compose as
# build arguments consumed by Dockerfile.vault-scanner.
#
# Environment overrides:
#
# - BUILD_SERVICE: Compose service to build. Defaults to vault-scanner-oneshot.
# - BUILD_PROFILE: Compose profile needed for the build service. Defaults to oneshot.
# - IMAGE_NAME: Docker image to verify after build. Defaults to vault-scanner:local.
# - SKIP_VERIFY: Set to true to skip post-build image stamp verification.

set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

build_service="${BUILD_SERVICE:-vault-scanner-oneshot}"
build_profile="${BUILD_PROFILE:-oneshot}"
image_name="${IMAGE_NAME:-vault-scanner:local}"

git_version_hash="$(git rev-parse HEAD)"
git_version_tag="$(git describe --tags --exact-match 2>/dev/null || true)"
git_commit_message="$(git log -1 --pretty=%s)"

export GIT_VERSION_HASH="$git_version_hash"
export GIT_VERSION_TAG="$git_version_tag"
export GIT_COMMIT_MESSAGE="$git_commit_message"

printf 'Building %s as %s\n' "$build_service" "$image_name"
printf 'Git commit: %s\n' "$GIT_VERSION_HASH"
if [[ -n "$GIT_VERSION_TAG" ]]; then
    printf 'Git tag: %s\n' "$GIT_VERSION_TAG"
else
    printf 'Git tag: <none>\n'
fi
printf 'Git commit message: %s\n' "$GIT_COMMIT_MESSAGE"

docker compose --profile "$build_profile" build "$build_service"

if [[ "${SKIP_VERIFY:-false}" == "true" ]]; then
    printf 'Skipping image stamp verification because SKIP_VERIFY=true\n'
    exit 0
fi

docker run \
    --rm \
    --entrypoint python \
    -e EXPECTED_GIT_VERSION_HASH="$GIT_VERSION_HASH" \
    -e EXPECTED_GIT_VERSION_TAG="$GIT_VERSION_TAG" \
    -e EXPECTED_GIT_COMMIT_MESSAGE="$GIT_COMMIT_MESSAGE" \
    "$image_name" \
    -c '
import os

from eth_defi.version_info import VersionInfo

version = VersionInfo.read_docker_version()
print(f"Stamped commit: {version.commit_hash}")
print(f"Stamped tag: {version.tag}")
print(f"Stamped commit message: {version.commit_message}")
assert version.commit_hash == os.environ["EXPECTED_GIT_VERSION_HASH"], (version.commit_hash, os.environ["EXPECTED_GIT_VERSION_HASH"])
assert version.commit_message == os.environ["EXPECTED_GIT_COMMIT_MESSAGE"], (version.commit_message, os.environ["EXPECTED_GIT_COMMIT_MESSAGE"])
expected_tag = os.environ["EXPECTED_GIT_VERSION_TAG"] or None
assert version.tag == expected_tag, (version.tag, expected_tag)
'

printf 'Image stamp verified for %s\n' "$image_name"
