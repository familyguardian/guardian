"""
Unit tests for the config module of guardian_daemon.
"""

import os
import tempfile

import pytest
import yaml

from guardian_daemon.config import Config, ConfigError


@pytest.fixture
def temp_config_file():
    """Create a temporary config file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        config = {
            "logging": {"level": "INFO", "format": "plain"},
            "db_path": "/tmp/test.db",
            "ipc_socket": "/tmp/test.sock",
            "reset_time": "03:00",
            "users": {
                "testuser": {
                    "quota": {"daily": 120, "weekly": 600},
                    "curfew": {
                        "weekday": {"start": "08:00", "end": "20:00"},
                        "weekend": {"start": "10:00", "end": "22:00"},
                    },
                    "grace_minutes": 5,
                }
            },
        }
        yaml.dump(config, f)
        temp_path = f.name

    yield temp_path

    # Cleanup
    if os.path.exists(temp_path):
        os.unlink(temp_path)


def test_config_validation_valid_config(temp_config_file):
    """Test that valid configuration passes validation."""
    config = Config(temp_config_file)
    assert config.data is not None
    assert "users" in config.data


def test_config_validation_invalid_log_level():
    """Test that invalid log level is rejected."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        config = {
            "logging": {"level": "INVALID", "format": "plain"},
            "db_path": "/tmp/test.db",
            "ipc_socket": "/tmp/test.sock",
        }
        yaml.dump(config, f)
        temp_path = f.name

    try:
        with pytest.raises(ConfigError) as exc_info:
            Config(temp_path)
        assert "Invalid log level" in str(exc_info.value)
    finally:
        os.unlink(temp_path)


def test_config_validation_invalid_time_format():
    """Test that invalid time formats are rejected."""
    invalid_times = [
        "25:00",  # Invalid hour
        "12:60",  # Invalid minute
        "12:5",  # Missing leading zero
        "12",  # Missing minutes
        "12:00:00",  # Too many parts
        "noon",  # Not a time
        12,  # Not a string
    ]

    for invalid_time in invalid_times:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            config = {
                "logging": {"level": "INFO"},
                "db_path": "/tmp/test.db",
                "ipc_socket": "/tmp/test.sock",
                "reset_time": invalid_time,
            }
            yaml.dump(config, f)
            temp_path = f.name

        try:
            with pytest.raises(ConfigError) as exc_info:
                Config(temp_path)
            assert "time format" in str(
                exc_info.value
            ).lower() or "must be a string" in str(exc_info.value)
        finally:
            os.unlink(temp_path)


def test_config_validation_negative_quota():
    """Test that negative quota values are rejected."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        config = {
            "logging": {"level": "INFO"},
            "db_path": "/tmp/test.db",
            "ipc_socket": "/tmp/test.sock",
            "users": {"testuser": {"quota": {"daily": -60}}},
        }
        yaml.dump(config, f)
        temp_path = f.name

    try:
        with pytest.raises(ConfigError) as exc_info:
            Config(temp_path)
        assert "must be non-negative" in str(exc_info.value)
    finally:
        os.unlink(temp_path)


def test_config_validation_invalid_quota_type():
    """Test that non-integer quota values are rejected."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        config = {
            "logging": {"level": "INFO"},
            "db_path": "/tmp/test.db",
            "ipc_socket": "/tmp/test.sock",
            "users": {"testuser": {"quota": {"daily": "sixty"}}},
        }
        yaml.dump(config, f)
        temp_path = f.name

    try:
        with pytest.raises(ConfigError) as exc_info:
            Config(temp_path)
        assert "must be an integer" in str(exc_info.value)
    finally:
        os.unlink(temp_path)


def test_config_validation_invalid_curfew_time():
    """Test that invalid curfew times are rejected."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        config = {
            "logging": {"level": "INFO"},
            "db_path": "/tmp/test.db",
            "ipc_socket": "/tmp/test.sock",
            "users": {
                "testuser": {"curfew": {"weekday": {"start": "25:00", "end": "20:00"}}}
            },
        }
        yaml.dump(config, f)
        temp_path = f.name

    try:
        with pytest.raises(ConfigError) as exc_info:
            Config(temp_path)
        assert "time format" in str(exc_info.value).lower()
    finally:
        os.unlink(temp_path)


def test_config_validation_valid_edge_cases():
    """Test that edge case valid values are accepted."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        config = {
            "logging": {"level": "DEBUG"},  # Valid log level
            "db_path": "/tmp/test.db",
            "ipc_socket": "/tmp/test.sock",
            "reset_time": "00:00",  # Midnight
            "users": {
                "testuser": {
                    "quota": {"daily": 0, "weekly": 0},  # Zero is valid
                    "curfew": {
                        "weekday": {"start": "00:00", "end": "23:59"}  # Full day
                    },
                }
            },
        }
        yaml.dump(config, f)
        temp_path = f.name

    try:
        config_obj = Config(temp_path)
        assert config_obj.data["reset_time"] == "00:00"
        assert config_obj.data["users"]["testuser"]["quota"]["daily"] == 0
    finally:
        os.unlink(temp_path)


def test_config_validation_invalid_grace_minutes():
    """Test that invalid grace_minutes values are rejected."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        config = {
            "logging": {"level": "INFO"},
            "db_path": "/tmp/test.db",
            "ipc_socket": "/tmp/test.sock",
            "users": {"testuser": {"grace_minutes": -5}},  # Negative grace minutes
        }
        yaml.dump(config, f)
        temp_path = f.name

    try:
        with pytest.raises(ConfigError) as exc_info:
            Config(temp_path)
        assert "grace_minutes" in str(exc_info.value) and (
            "must be positive" in str(exc_info.value)
            or "must be non-negative" in str(exc_info.value)
        )
    finally:
        os.unlink(temp_path)


def test_validate_time_format_method():
    """Test the time format validation method directly."""
    # Valid times
    Config._validate_time_format("00:00", "test")
    Config._validate_time_format("12:30", "test")
    Config._validate_time_format("23:59", "test")

    # Invalid times
    with pytest.raises(ConfigError):
        Config._validate_time_format("24:00", "test")

    with pytest.raises(ConfigError):
        Config._validate_time_format("12:60", "test")

    with pytest.raises(ConfigError):
        Config._validate_time_format("invalid", "test")


def test_validate_positive_integer_method():
    """Test the positive integer validation method directly."""
    # Valid integers
    Config._validate_positive_integer(1, "test")
    Config._validate_positive_integer(100, "test")
    Config._validate_positive_integer(0, "test", allow_zero=True)

    # Invalid
    with pytest.raises(ConfigError):
        Config._validate_positive_integer(0, "test", allow_zero=False)

    with pytest.raises(ConfigError):
        Config._validate_positive_integer(-1, "test")

    with pytest.raises(ConfigError):
        Config._validate_positive_integer("10", "test")

    with pytest.raises(ConfigError):
        Config._validate_positive_integer(10.5, "test")
