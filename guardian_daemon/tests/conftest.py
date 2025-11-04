"""
Configuration and fixtures for guardian_daemon tests.
"""

import os
import tempfile

import pytest
import yaml


@pytest.fixture
def test_config():
    """
    Provides a test configuration with temporary paths and test users.
    """
    # Static test configuration with all possible settings
    config = {
        "users": {
            "test_full_settings": {
                "quota": {
                    "daily": 120,  # 2 hours
                    "weekly": 600,  # 10 hours
                    "monthly": 2400,  # 40 hours
                },
                "curfew": {
                    "weekday": {"start": "08:00", "end": "20:00"},
                    "weekend": {"start": "10:00", "end": "22:00"},
                },
                "bonus_pool": 60,  # 1 hour bonus time
                "grace_period": 15,  # 15 minutes grace period
            },
            "test_quota_only": {
                "quota": {"daily": 60, "weekly": 300}  # 1 hour  # 5 hours
            },
            "test_weekday_curfew": {
                "curfew": {"weekday": {"start": "09:00", "end": "21:00"}}
            },
            "test_minimal": {"quota": {"daily": 30}},  # Only daily quota, no weekly
            "test_quota_exempt": {"quota_exempt": True},
            "test_unmonitored": {"monitored": False},
        },
        # System settings
        "db_path": None,  # Will be set by fixture
        "ipc_socket": None,  # Will be set by fixture
        "hub_address": "https://test-hub.example.com",
        # Time settings
        "reset_time": "03:00",
        "timezone": "Europe/Berlin",
        # Default settings
        "defaults": {
            "daily_quota_minutes": 90,
            "weekly_quota_minutes": 600,
            "grace_minutes": 5,
            "bonus_pool_minutes": 30,
            "curfew": {
                "weekdays": "08:00-20:00",
                "saturday": "10:00-22:00",
                "sunday": "10:00-20:00",
            },
        },
        # Notification settings
        "notifications": {
            "pre_quota_minutes": [15, 10, 5],  # Warning times before quota expires
            "grace_period": {"enabled": True, "duration": 10, "interval": 1},
        },
        # Logging settings
        "logging": {"level": "DEBUG", "format": "plain", "target": "stdout"},
    }

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, "guardian.sqlite")
        config["db_path"] = db_path
        config["ipc_socket"] = os.path.join(temp_dir, "guardian-daemon.sock")
        config_path = os.path.join(temp_dir, "config.yaml")
        with open(config_path, "w") as f:
            yaml.dump(config, f)
        yield config, config_path


@pytest.fixture
def mock_dbus(mocker):
    """
    Mocks DBus connections and interfaces for testing.
    """
    mock_bus = mocker.MagicMock()
    mock_logind = mocker.MagicMock()
    mock_bus.get_proxy.return_value = mock_logind
    return mock_bus, mock_logind
