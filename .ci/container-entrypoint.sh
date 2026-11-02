#!/usr/bin/env bash
set -euo pipefail

: "${LOCAL_UID:?LOCAL_UID is required}"
: "${LOCAL_GID:?LOCAL_GID is required}"

if [[ ! "${LOCAL_UID}" =~ ^[0-9]+$ || ! "${LOCAL_GID}" =~ ^[0-9]+$ ]]; then
    echo "LOCAL_UID and LOCAL_GID must be numeric" >&2
    exit 2
fi

if [[ "$(id -u)" != "0" ]]; then
    echo "container entrypoint must start as root before dropping to ${LOCAL_UID}:${LOCAL_GID}" >&2
    exit 126
fi

# Only the Docker volume is prepared here.  /workdir and /config are bind
# mounts and are deliberately never modified by the privileged setup phase.
install -d -o "${LOCAL_UID}" -g "${LOCAL_GID}" \
    /cache/build /cache/build/downloads /cache/build/sstate-cache "${HOME}"
touch /cache/.catplay-build.lock
chown "${LOCAL_UID}:${LOCAL_GID}" /cache/.catplay-build.lock
if [[ -e /cache/.catplay-operation ]]; then
    chown "${LOCAL_UID}:${LOCAL_GID}" /cache/.catplay-operation
fi
touch /tmp/catplay-container-ready

exec setpriv \
    --reuid="${LOCAL_UID}" \
    --regid="${LOCAL_GID}" \
    --clear-groups \
    -- "$@"
