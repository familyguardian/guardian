"""Fixtures for the L3 PAM curfew test layer.

These exercise the *real* pam_time.so module: guardian's _generate_rules() output
is written to /etc/security/time.conf, then pamtester runs the PAM account phase
at controlled wall-clock times (via faketime) to assert that login is actually
allowed/denied at curfew boundaries. This validates the curfew mechanism that the
mock-based unit tests can only assert in the abstract.

Requires root (to edit /etc/security/time.conf and /etc/pam.d) and pam_time.so;
otherwise the pam-marked tests are skipped.
"""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

TIME_CONF = Path("/etc/security/time.conf")
PAM_SERVICE_NAME = "guardian_test"
PAM_SERVICE_FILE = Path("/etc/pam.d") / PAM_SERVICE_NAME

_PAM_TIME_CANDIDATES = [
    Path("/usr/lib/x86_64-linux-gnu/security/pam_time.so"),
    Path("/lib/x86_64-linux-gnu/security/pam_time.so"),
    Path("/usr/lib64/security/pam_time.so"),
    Path("/lib/security/pam_time.so"),
]


def _pam_available() -> bool:
    if os.geteuid() != 0:
        return False
    if not any(p.exists() for p in _PAM_TIME_CANDIDATES):
        return False
    for tool in ("pamtester", "faketime"):
        if (
            subprocess.run(["which", tool], capture_output=True).returncode != 0
        ):  # noqa: PLW1510
            return False
    return True


# Module-level skip condition reused by the pam test modules.
requires_pam = pytest.mark.skipif(
    not _pam_available(),
    reason="needs root + pam_time.so + pamtester + faketime",
)


@pytest.fixture
def make_user_manager(tmp_path):
    """Build a real UserManager backed by a temporary policy/DB."""
    from guardian_daemon.policy import Policy
    from guardian_daemon.user_manager import UserManager

    def _make(users, defaults=None):
        defaults = defaults or {
            "curfew": {
                "weekdays": "08:00-20:00",
                "saturday": "09:00-22:00",
                "sunday": "09:00-20:00",
            }
        }
        config = {
            "users": users,
            "defaults": defaults,
            "db_path": str(tmp_path / "guardian.sqlite"),
            "timezone": "UTC",
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config))
        policy = Policy(str(config_path))
        return UserManager(policy)

    return _make


@pytest.fixture
def pam_curfew():
    """Install the test PAM service and isolate /etc/security/time.conf.

    Yields a callable ``check(username, rules, when)`` that writes the given rule
    lines to time.conf and runs ``pamtester`` for the account phase at the given
    faketime moment, returning True if login is allowed (exit code 0).
    """
    if not _pam_available():
        pytest.skip("needs root + pam_time.so + pamtester + faketime")

    backup = TIME_CONF.read_bytes() if TIME_CONF.exists() else None
    TIME_CONF.parent.mkdir(parents=True, exist_ok=True)
    # pam_time returns PAM_IGNORE when no rule matches the user; a pam_permit
    # fallback turns that into "allow" - mirroring how pam_time is layered onto a
    # real account stack (e.g. SDDM's). Without it, a no-match would deny.
    PAM_SERVICE_FILE.write_text(
        "account required pam_time.so\naccount required pam_permit.so\n"
    )

    def check(username, rules, when=None):
        TIME_CONF.write_text("\n".join(rules) + "\n")
        cmd = []
        if when is not None:
            cmd += ["faketime", when]
        # The PAM account-management phase is "acct_mgmt" in pamtester.
        cmd += ["pamtester", PAM_SERVICE_NAME, username, "acct_mgmt"]
        env = dict(os.environ, TZ="UTC")
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
        return proc.returncode == 0, proc

    try:
        yield check
    finally:
        if backup is not None:
            TIME_CONF.write_bytes(backup)
        elif TIME_CONF.exists():
            TIME_CONF.unlink()
        if PAM_SERVICE_FILE.exists():
            PAM_SERVICE_FILE.unlink()
