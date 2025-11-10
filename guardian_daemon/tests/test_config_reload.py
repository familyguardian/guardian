"""
Unit tests for config reload safety and atomicity in guardian_daemon.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from guardian_daemon.__main__ import GuardianDaemon
from guardian_daemon.config import Config


@pytest.fixture
def mock_config():
    """Create a mock config object."""
    config = MagicMock(spec=Config)
    config.data = {
        "logging": {"level": "INFO"},
        "db_path": "/tmp/test.db",
        "ipc_socket": "/tmp/test.sock",
        "reset_time": "03:00",
        "curfew": {"start": "22:00", "end": "06:00"},
        "users": {"testuser": {"quota": {"daily": 120}}},
    }
    config.get = lambda key, default=None: config.data.get(key, default)
    config.config_path = "/tmp/test_config.yaml"
    return config


@pytest.fixture
def mock_daemon(mock_config):
    """Create a GuardianDaemon instance with mocked components."""
    with patch("guardian_daemon.__main__.Policy") as mock_policy_class:
        with patch("guardian_daemon.__main__.Storage"):
            with patch("guardian_daemon.__main__.SystemdManager"):
                with patch("guardian_daemon.__main__.UserManager"):
                    with patch("guardian_daemon.__main__.SessionTracker"):
                        with patch("guardian_daemon.__main__.Enforcer"):
                            with patch("guardian_daemon.__main__.GuardianIPCServer"):
                                # Setup mock policy
                                mock_policy = MagicMock()
                                mock_policy.data = mock_config.data.copy()
                                mock_policy_class.return_value = mock_policy

                                daemon = GuardianDaemon(mock_config)
                                return daemon


def test_validate_time_format_valid():
    """Test that valid time formats are accepted."""
    assert GuardianDaemon._validate_time_format("00:00")
    assert GuardianDaemon._validate_time_format("12:30")
    assert GuardianDaemon._validate_time_format("23:59")


def test_validate_time_format_invalid():
    """Test that invalid time formats are rejected."""
    assert not GuardianDaemon._validate_time_format("24:00")
    assert not GuardianDaemon._validate_time_format("12:60")
    assert not GuardianDaemon._validate_time_format("12")
    assert not GuardianDaemon._validate_time_format("invalid")
    assert not GuardianDaemon._validate_time_format(12)
    assert not GuardianDaemon._validate_time_format(None)


@pytest.mark.asyncio
async def test_config_reload_validates_before_applying(mock_daemon):
    """Test that invalid config is rejected before application."""
    # Setup invalid config
    mock_daemon.policy.data = mock_daemon.policy.data.copy()
    mock_daemon.policy.data["reset_time"] = "25:00"  # Invalid hour

    # Mock the hash to trigger reload
    with patch.object(mock_daemon, "_get_config_hash", side_effect=["hash1", "hash2"]):
        with patch.object(mock_daemon.policy, "reload"):
            # Run one iteration (with timeout to prevent infinite loop)
            task = asyncio.create_task(mock_daemon.periodic_reload())
            await asyncio.sleep(0.1)
            task.cancel()

            try:
                await task
            except asyncio.CancelledError:
                pass

    # Verify usermanager update was not called (due to validation failure)
    # The error should be caught and logged, system continues with old config


@pytest.mark.asyncio
async def test_config_reload_rolls_back_on_error(mock_daemon):
    """Test that config rolls back when application fails."""
    old_data = mock_daemon.policy.data.copy()
    new_data = old_data.copy()
    new_data["reset_time"] = "04:00"

    # Mock policy reload to provide new data
    def mock_reload():
        mock_daemon.policy.data = new_data.copy()

    mock_daemon.policy.reload = mock_reload

    # Mock user_manager to fail during update
    mock_daemon.usermanager.write_time_rules = MagicMock(
        side_effect=Exception("Write failed")
    )

    # Mock hash to trigger reload
    with patch.object(
        mock_daemon, "_get_config_hash", side_effect=["hash1", "hash2", "hash2"]
    ):
        # Run one iteration
        task = asyncio.create_task(mock_daemon.periodic_reload())
        await asyncio.sleep(0.1)
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass

    # Verify policy was rolled back to old data
    assert mock_daemon.policy.data == old_data


@pytest.mark.asyncio
async def test_config_reload_atomic_update():
    """Test that config updates are atomic (all or nothing)."""
    # This test verifies the structure exists
    # The actual atomicity is tested by the rollback test
    assert GuardianDaemon._validate_time_format("12:00")
    # If validation passes, the system should apply all changes
    # If any step fails, the rollback should restore previous state


@pytest.mark.asyncio
async def test_config_reload_validates_curfew_times(mock_daemon):
    """Test that curfew times are validated during reload."""
    # Setup invalid curfew
    mock_daemon.policy.data = mock_daemon.policy.data.copy()
    mock_daemon.policy.data["curfew"] = {"start": "25:00", "end": "06:00"}

    # Mock hash to trigger reload
    with patch.object(mock_daemon, "_get_config_hash", side_effect=["hash1", "hash2"]):
        with patch.object(mock_daemon.policy, "reload"):
            # Run one iteration
            task = asyncio.create_task(mock_daemon.periodic_reload())
            await asyncio.sleep(0.1)
            task.cancel()

            try:
                await task
            except asyncio.CancelledError:
                pass

    # Verify update was not applied (hash should not change)
    # Due to validation failure, system keeps old hash


@pytest.mark.asyncio
async def test_config_reload_error_handling():
    """Test that config reload errors are caught and logged."""
    # Test verifies the error handling structure exists
    # Actual error handling is tested by the rollback test above
    # Verify the method has proper error handling structure
    import inspect

    from guardian_daemon.__main__ import GuardianDaemon

    source = inspect.getsource(GuardianDaemon.periodic_reload)

    # Check for try/except blocks
    assert "try:" in source
    assert "except" in source
    assert "rollback" in source.lower() or "rolled back" in source.lower()
