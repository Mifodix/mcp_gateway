#!/usr/bin/env sh
set -eu

VERSION="${MICROSANDBOX_VERSION:-0.8.2}"

if [ "$(uname -s)" != "Linux" ]; then
    echo "This installer is intended for Linux." >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required on the host." >&2
    exit 1
fi

if [ ! -e /dev/kvm ]; then
    echo "/dev/kvm is missing. Enable hardware virtualization and KVM." >&2
    exit 1
fi

if [ ! -r /dev/kvm ] || [ ! -w /dev/kvm ]; then
    echo "No read/write access to /dev/kvm. Add the spike user to the kvm group or adjust the device permissions." >&2
    exit 1
fi

if ! command -v msb >/dev/null 2>&1; then
    if ! command -v uv >/dev/null 2>&1; then
        echo "uv is required for the pinned installation. Install uv, then rerun this script." >&2
        echo "Version to install: microsandbox==${VERSION}" >&2
        exit 1
    fi
    uv tool install "microsandbox==${VERSION}"
fi

echo "Installed: $(msb --version)"
echo "Expected major version for this spike: ${VERSION}"
