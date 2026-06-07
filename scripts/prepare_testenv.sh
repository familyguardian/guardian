#!/usr/bin/env bash
# Backwards-compatible shim. The provisioning logic now lives in
# setup_testenv.sh (installs system deps, syncs uv, runs smoke-checks).
exec "$(dirname "${BASH_SOURCE[0]}")/setup_testenv.sh" "$@"
