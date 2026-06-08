"""L3b: golden-text tests for systemd unit/timer generation.

These tests assert the exact text returned by the pure render functions
extracted from SystemdManager.  No running systemd is required; the optional
``systemd-analyze verify`` checks only parse the unit files statically.
"""

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from guardian_daemon.systemd_manager import (
    SystemdManager,
    _render_curfew_service,
    _render_curfew_timer,
    _render_daily_reset_service,
    _render_daily_reset_timer,
)


def _systemd_analyze_available() -> bool:
    if shutil.which("systemd-analyze") is None:
        return False
    try:
        r = subprocess.run(
            ["systemd-analyze", "--version"], capture_output=True, timeout=5
        )
        return r.returncode == 0
    except Exception:
        return False


requires_systemd_analyze = pytest.mark.skipif(
    not _systemd_analyze_available(),
    reason="systemd-analyze not available",
)

pytestmark = pytest.mark.systemd_units

# ---------------------------------------------------------------------------
# Golden text constants
# ---------------------------------------------------------------------------

DAILY_RESET_SERVICE_GOLDEN = """
[Unit]
Description=Guardian daily quota reset

[Service]
Type=oneshot
ExecStart=/usr/bin/guardianctl reset-quota
"""

DAILY_RESET_TIMER_GOLDEN = """
[Unit]
Description=Guardian daily quota reset timer

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
"""

CURFEW_SERVICE_GOLDEN = """
[Unit]
Description=Guardian curfew enforcement

[Service]
Type=oneshot
ExecStart=/usr/bin/guardianctl enforce-curfew
"""

CURFEW_TIMER_GOLDEN = """
[Unit]
Description=Guardian curfew enforcement timer

[Timer]
OnCalendar=*-*-* 22:00:00
OnCalendar=*-*-* 06:00:00
Persistent=true

[Install]
WantedBy=timers.target
"""

# ---------------------------------------------------------------------------
# Render function unit tests
# ---------------------------------------------------------------------------


def test_daily_reset_service_golden():
    assert _render_daily_reset_service() == DAILY_RESET_SERVICE_GOLDEN


def test_daily_reset_timer_default():
    assert _render_daily_reset_timer("03:00") == DAILY_RESET_TIMER_GOLDEN


@pytest.mark.parametrize("reset_time", ["00:00", "12:30", "23:59"])
def test_daily_reset_timer_parametrized(reset_time):
    text = _render_daily_reset_timer(reset_time)
    assert f"OnCalendar=*-*-* {reset_time}:00" in text
    assert text.startswith("\n[Unit]")
    assert text.endswith("\n")


def test_curfew_service_golden():
    assert _render_curfew_service() == CURFEW_SERVICE_GOLDEN


def test_curfew_timer_default():
    assert _render_curfew_timer("22:00", "06:00") == CURFEW_TIMER_GOLDEN


@pytest.mark.parametrize(
    "start_time,end_time",
    [
        ("22:00", "06:00"),
        ("00:00", "23:59"),
        ("08:00", "20:00"),
        ("20:00", "08:00"),
    ],
)
def test_curfew_timer_parametrized(start_time, end_time):
    text = _render_curfew_timer(start_time, end_time)
    assert f"OnCalendar=*-*-* {start_time}:00" in text
    assert f"OnCalendar=*-*-* {end_time}:00" in text
    assert text.count("OnCalendar=") == 2
    assert text.startswith("\n[Unit]")
    assert text.endswith("\n")


def test_render_functions_produce_valid_ini_sections():
    """Every rendered unit must contain the expected INI section headers."""
    assert "[Unit]" in _render_daily_reset_service()
    assert "[Service]" in _render_daily_reset_service()

    assert "[Unit]" in _render_daily_reset_timer("03:00")
    assert "[Timer]" in _render_daily_reset_timer("03:00")
    assert "[Install]" in _render_daily_reset_timer("03:00")

    assert "[Unit]" in _render_curfew_service()
    assert "[Service]" in _render_curfew_service()

    assert "[Unit]" in _render_curfew_timer("22:00", "06:00")
    assert "[Timer]" in _render_curfew_timer("22:00", "06:00")
    assert "[Install]" in _render_curfew_timer("22:00", "06:00")


# ---------------------------------------------------------------------------
# Integration: SystemdManager uses render functions when writing files
# ---------------------------------------------------------------------------


def test_create_daily_reset_timer_writes_expected_content(tmp_path):
    mgr = SystemdManager()
    with patch("guardian_daemon.systemd_manager.SYSTEMD_PATH", tmp_path):
        mgr.create_daily_reset_timer("03:00")

    service_text = (tmp_path / "guardian-daily-reset.service").read_text()
    timer_text = (tmp_path / "guardian-daily-reset.timer").read_text()

    assert service_text == _render_daily_reset_service()
    assert timer_text == _render_daily_reset_timer("03:00")


def test_create_curfew_timer_writes_expected_content(tmp_path):
    mgr = SystemdManager()
    with patch("guardian_daemon.systemd_manager.SYSTEMD_PATH", tmp_path):
        mgr.create_curfew_timer("22:00", "06:00")

    service_text = (tmp_path / "guardian-curfew.service").read_text()
    timer_text = (tmp_path / "guardian-curfew.timer").read_text()

    assert service_text == _render_curfew_service()
    assert timer_text == _render_curfew_timer("22:00", "06:00")


def test_create_daily_reset_timer_invalid_time_skips_write(tmp_path):
    mgr = SystemdManager()
    with patch("guardian_daemon.systemd_manager.SYSTEMD_PATH", tmp_path):
        mgr.create_daily_reset_timer("25:00")
    assert not (tmp_path / "guardian-daily-reset.service").exists()
    assert not (tmp_path / "guardian-daily-reset.timer").exists()


def test_create_curfew_timer_invalid_time_skips_write(tmp_path):
    mgr = SystemdManager()
    with patch("guardian_daemon.systemd_manager.SYSTEMD_PATH", tmp_path):
        mgr.create_curfew_timer("99:00", "06:00")
    assert not (tmp_path / "guardian-curfew.service").exists()
    assert not (tmp_path / "guardian-curfew.timer").exists()


# ---------------------------------------------------------------------------
# Optional: systemd-analyze verify (static syntax check, no daemon required)
# ---------------------------------------------------------------------------


def _verify_unit(tmp_path: Path, filename: str, content: str) -> None:
    """Run systemd-analyze verify and fail only on real syntax/parse errors.

    systemd-analyze also warns when ExecStart binaries are missing; that check
    is irrelevant to unit syntax and will fire in CI where guardianctl is not
    installed, so we filter it out and only fail on genuine parse errors
    (lines that do NOT mention "not executable" or "No such file").
    """
    unit_file = tmp_path / filename
    unit_file.write_text(content)
    result = subprocess.run(
        ["systemd-analyze", "verify", str(unit_file)],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return
    # Allow the specific "binary not found" warning that fires when guardianctl
    # is not installed; fail for any other diagnostic line.
    ignorable = {"not executable", "No such file or directory"}
    real_errors = [
        line
        for line in result.stderr.splitlines()
        if line.strip() and not any(pat in line for pat in ignorable)
    ]
    assert (
        not real_errors
    ), f"systemd-analyze verify found errors in {filename}:\n" + "\n".join(real_errors)


@requires_systemd_analyze
def test_daily_reset_service_passes_systemd_analyze(tmp_path):
    _verify_unit(
        tmp_path, "guardian-daily-reset.service", _render_daily_reset_service()
    )


@requires_systemd_analyze
def test_daily_reset_timer_passes_systemd_analyze(tmp_path):
    _verify_unit(
        tmp_path, "guardian-daily-reset.timer", _render_daily_reset_timer("03:00")
    )


@requires_systemd_analyze
def test_curfew_service_passes_systemd_analyze(tmp_path):
    _verify_unit(tmp_path, "guardian-curfew.service", _render_curfew_service())


@requires_systemd_analyze
def test_curfew_timer_passes_systemd_analyze(tmp_path):
    _verify_unit(
        tmp_path, "guardian-curfew.timer", _render_curfew_timer("22:00", "06:00")
    )
