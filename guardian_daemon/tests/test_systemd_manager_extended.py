"""
Extended test coverage for SystemdManager in guardian_daemon.
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from guardian_daemon.systemd_manager import SystemdManager, _is_valid_time_format


def test_is_valid_time_format_valid():
    """Test time format validation with valid times."""
    assert _is_valid_time_format("00:00")
    assert _is_valid_time_format("12:30")
    assert _is_valid_time_format("23:59")
    assert _is_valid_time_format("03:00")


def test_is_valid_time_format_invalid():
    """Test time format validation with invalid times."""
    assert not _is_valid_time_format("24:00")
    assert not _is_valid_time_format("12:60")
    assert not _is_valid_time_format("invalid")
    assert not _is_valid_time_format("12")
    assert not _is_valid_time_format(None)
    assert not _is_valid_time_format(1200)


def test_systemd_manager_init():
    """Test SystemdManager initialization."""
    manager = SystemdManager()
    assert manager is not None


def test_create_daily_reset_timer_success():
    """Test successful creation of daily reset timer."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("guardian_daemon.systemd_manager.SYSTEMD_PATH", Path(tmpdir)):
            manager = SystemdManager()
            manager.create_daily_reset_timer("03:00")

            # Check that files were created
            timer_file = Path(tmpdir) / "guardian-daily-reset.timer"
            service_file = Path(tmpdir) / "guardian-daily-reset.service"

            assert timer_file.exists()
            assert service_file.exists()

            # Check content
            timer_content = timer_file.read_text()
            assert "OnCalendar=*-*-* 03:00:00" in timer_content
            assert "timers.target" in timer_content

            service_content = service_file.read_text()
            assert "guardianctl reset-quota" in service_content


def test_create_daily_reset_timer_invalid_time():
    """Test that invalid time format is rejected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("guardian_daemon.systemd_manager.SYSTEMD_PATH", Path(tmpdir)):
            manager = SystemdManager()
            manager.create_daily_reset_timer("25:00")  # Invalid hour

            # Should not create files
            timer_file = Path(tmpdir) / "guardian-daily-reset.timer"
            assert not timer_file.exists()


def test_create_daily_reset_timer_write_error():
    """Test handling of file write errors."""
    with patch(
        "guardian_daemon.systemd_manager.SYSTEMD_PATH", Path("/nonexistent/path")
    ):
        manager = SystemdManager()
        # Should log error but not raise exception
        manager.create_daily_reset_timer("03:00")


def test_create_curfew_timer_success():
    """Test successful creation of curfew timer."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("guardian_daemon.systemd_manager.SYSTEMD_PATH", Path(tmpdir)):
            manager = SystemdManager()
            manager.create_curfew_timer("22:00", "06:00")

            # Check that files were created
            timer_file = Path(tmpdir) / "guardian-curfew.timer"
            service_file = Path(tmpdir) / "guardian-curfew.service"

            assert timer_file.exists()
            assert service_file.exists()

            # Check content
            timer_content = timer_file.read_text()
            assert "OnCalendar=*-*-* 22:00:00" in timer_content
            assert "OnCalendar=*-*-* 06:00:00" in timer_content

            service_content = service_file.read_text()
            assert "guardianctl enforce-curfew" in service_content


def test_create_curfew_timer_invalid_start_time():
    """Test curfew timer rejects invalid start time."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("guardian_daemon.systemd_manager.SYSTEMD_PATH", Path(tmpdir)):
            manager = SystemdManager()
            manager.create_curfew_timer("invalid", "06:00")

            timer_file = Path(tmpdir) / "guardian-curfew.timer"
            assert not timer_file.exists()


def test_create_curfew_timer_invalid_end_time():
    """Test curfew timer rejects invalid end time."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("guardian_daemon.systemd_manager.SYSTEMD_PATH", Path(tmpdir)):
            manager = SystemdManager()
            manager.create_curfew_timer("22:00", "25:00")

            timer_file = Path(tmpdir) / "guardian-curfew.timer"
            assert not timer_file.exists()


def test_create_curfew_timer_write_error():
    """Test handling of curfew timer write errors."""
    with patch(
        "guardian_daemon.systemd_manager.SYSTEMD_PATH", Path("/nonexistent/path")
    ):
        manager = SystemdManager()
        # Should log error but not raise exception
        manager.create_curfew_timer("22:00", "06:00")


@pytest.mark.asyncio
async def test_reload_systemd_success():
    """Test successful systemd reload."""
    manager = SystemdManager()

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = mock_proc

        await manager.reload_systemd()

        # Should have called systemctl daemon-reload
        mock_exec.assert_called_once_with(
            "systemctl",
            "daemon-reload",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )


@pytest.mark.asyncio
async def test_reload_systemd_failure():
    """Test systemd reload with non-zero exit code."""
    manager = SystemdManager()

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"Error message"))

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = mock_proc

        # Should not raise exception
        await manager.reload_systemd()


@pytest.mark.asyncio
async def test_reload_systemd_timeout():
    """Test systemd reload with timeout."""
    manager = SystemdManager()

    mock_proc = MagicMock()
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    async def slow_communicate():
        await asyncio.sleep(20)  # Longer than 10s timeout
        return (b"", b"")

    mock_proc.communicate = slow_communicate

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = mock_proc

        await manager.reload_systemd()

        # Should have killed the process
        mock_proc.kill.assert_called_once()


@pytest.mark.asyncio
async def test_reload_systemd_exception():
    """Test systemd reload with exception."""
    manager = SystemdManager()

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.side_effect = Exception("Test error")

        # Should handle exception gracefully
        await manager.reload_systemd()


@pytest.mark.asyncio
async def test_reload_systemd_outer_timeout():
    """Test systemd reload when outer try block gets TimeoutError.

    This covers the outer except asyncio.TimeoutError handler (line 158)
    which is distinct from the inner timeout handling around wait_for().
    """
    manager = SystemdManager()

    # Make create_subprocess_exec raise TimeoutError directly
    # This simulates a timeout in the outer try block before wait_for()
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.side_effect = asyncio.TimeoutError("Subprocess creation timed out")

        # Should handle timeout gracefully without crashing
        await manager.reload_systemd()


def test_remove_timer_and_service_both_exist():
    """Test removing timer and service when both exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("guardian_daemon.systemd_manager.SYSTEMD_PATH", Path(tmpdir)):
            # Create dummy files
            timer_file = Path(tmpdir) / "test-timer.timer"
            service_file = Path(tmpdir) / "test-timer.service"
            timer_file.write_text("timer content")
            service_file.write_text("service content")

            manager = SystemdManager()
            manager.remove_timer_and_service("test-timer")

            # Files should be removed
            assert not timer_file.exists()
            assert not service_file.exists()


def test_remove_timer_and_service_only_timer_exists():
    """Test removing when only timer exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("guardian_daemon.systemd_manager.SYSTEMD_PATH", Path(tmpdir)):
            # Create only timer file
            timer_file = Path(tmpdir) / "test-timer.timer"
            timer_file.write_text("timer content")

            manager = SystemdManager()
            manager.remove_timer_and_service("test-timer")

            # Timer should be removed
            assert not timer_file.exists()


def test_remove_timer_and_service_neither_exists():
    """Test removing when neither file exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("guardian_daemon.systemd_manager.SYSTEMD_PATH", Path(tmpdir)):
            manager = SystemdManager()
            # Should not raise exception
            manager.remove_timer_and_service("nonexistent")


def test_remove_timer_and_service_permission_error():
    """Test handling of permission errors during removal."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("guardian_daemon.systemd_manager.SYSTEMD_PATH", Path(tmpdir)):
            timer_file = Path(tmpdir) / "test-timer.timer"
            timer_file.write_text("timer content")

            manager = SystemdManager()

            # Mock unlink to raise PermissionError
            with patch.object(
                Path, "unlink", side_effect=PermissionError("No permission")
            ):
                # Should handle error gracefully
                manager.remove_timer_and_service("test-timer")
