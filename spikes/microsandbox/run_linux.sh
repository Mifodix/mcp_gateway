#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
IMAGE="${MSB_IMAGE:-python:3.12}"
MEMORY="${MSB_MEMORY:-512M}"
RESULTS="${1:-${SCRIPT_DIR}/results-linux.json}"

"${SCRIPT_DIR}/install_linux.sh"

set -- python3 "${SCRIPT_DIR}/spike.py" \
    --image "${IMAGE}" \
    --memory "${MEMORY}" \
    --results "${RESULTS}"

if [ "${COW_MEMORY:-0}" = "1" ]; then
    set -- "$@" --cow-memory
fi

exec "$@"
