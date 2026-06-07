#!/usr/bin/env bash
#
# Provision the environment so Guardian's full automated test suite can run
# headlessly - in the Claude Cloud Dev Environment, in CI, or on a developer's
# machine. Idempotent: safe to run at the start of every session.
#
# Installs the system packages the integration layers need (a real D-Bus broker,
# notify-send, pamtester, pam_time.so/pam_permit.so, faketime), syncs the uv
# workspace, then runs capability smoke-checks and fails loudly if a layer would
# be unable to run. See TESTING.md for the layer overview.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\n\033[1;34m[setup_testenv]\033[0m %s\n' "$*"; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; fi

# --- 1. System packages (only touch apt if something is actually missing) ---
APT_PACKAGES=(
  dbus           # dbus-daemon broker for the L2 D-Bus contract tests
  libnotify-bin  # notify-send, exercised by the agent notification path
  pamtester      # drives the PAM account phase in the L3 curfew tests
  libpam-modules # provides pam_time.so + pam_permit.so
  faketime       # time travel for curfew-boundary assertions
)
missing=0
for cmd in dbus-daemon notify-send pamtester faketime; do
  command -v "$cmd" >/dev/null 2>&1 || missing=1
done
if [ "$missing" -eq 1 ]; then
  if command -v apt-get >/dev/null 2>&1; then
    log "Installing system test dependencies via apt-get..."
    export DEBIAN_FRONTEND=noninteractive
    $SUDO apt-get update -qq
    $SUDO apt-get install -y -qq "${APT_PACKAGES[@]}"
  else
    log "WARNING: apt-get unavailable and tools missing; install the equivalents of: ${APT_PACKAGES[*]}"
  fi
else
  log "System test dependencies already present; skipping apt."
fi

# --- 2. Python toolchain + workspace dependencies ---
log "Installing Python 3.12 and syncing the uv workspace (all packages + groups)..."
uv python install 3.12
uv sync --all-packages --all-groups

# --- 3. Capability smoke-checks (fail loudly) ---
log "Verifying test capabilities..."
ok=1
check() {
  if eval "$2" >/dev/null 2>&1; then
    printf '  [ ok ] %s\n' "$1"
  else
    printf '  [FAIL] %s\n' "$1"
    ok=0
  fi
}

check "uv venv usable"             "uv run python -c 'import sys'"
check "daemon unit tests collect"  "(cd guardian_daemon && uv run pytest --collect-only -q -p no:cacheprovider)"
check "dbus-next on a real bus"    "dbus-run-session -- uv run python -c 'import dbus_next'"
check "notify-send present"        "command -v notify-send"
check "pamtester present"          "command -v pamtester"
check "faketime present"           "command -v faketime"
check "pam_time.so present"        "ls /usr/lib/*/security/pam_time.so /lib/*/security/pam_time.so /usr/lib64/security/pam_time.so"

if [ "$ok" -ne 1 ]; then
  log "One or more capability checks FAILED - some test layers cannot run here."
  exit 1
fi

log "Environment ready. Run tests with:"
cat <<'EOF'
    # fast unit layer (per package)
    (cd guardian_daemon && uv run pytest)
    # D-Bus contract layer (L2)
    dbus-run-session -- uv run pytest tests/dbus -m dbus
    # PAM curfew layer (L3, needs root)
    uv run pytest tests/pam -m pam
EOF
