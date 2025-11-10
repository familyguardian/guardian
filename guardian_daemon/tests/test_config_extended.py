"""
Extended test coverage for Config module to reach 50% total coverage.
"""

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from guardian_daemon.config import Config, ConfigError


def test_config_load_with_explicit_path():
    """Test loading config with explicit path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "test_config.yaml"
        config_data = {
            "db_path": "/tmp/test.db",
            "ipc_socket": "/tmp/test.sock",
            "logging": {"level": "DEBUG"},
            "users": {"testuser": {"quota": {"daily": 60}}},
        }
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        config = Config(str(config_path))
        assert config.get("db_path") == "/tmp/test.db"
        assert config["ipc_socket"] == "/tmp/test.sock"


def test_config_load_from_env_variable():
    """Test loading config from GUARDIAN_DAEMON_CONFIG environment variable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "env_config.yaml"
        config_data = {
            "db_path": "/tmp/env_test.db",
            "ipc_socket": "/tmp/env_test.sock",
            "logging": {"level": "INFO"},
        }
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        os.environ["GUARDIAN_DAEMON_CONFIG"] = str(config_path)
        try:
            config = Config()
            assert config.get("db_path") == "/tmp/env_test.db"
        finally:
            del os.environ["GUARDIAN_DAEMON_CONFIG"]


def test_config_validation_invalid_log_level():
    """Test that invalid log level raises ConfigError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "invalid_config.yaml"
        config_data = {
            "db_path": "/tmp/test.db",
            "ipc_socket": "/tmp/test.sock",
            "logging": {"level": "INVALID_LEVEL"},
        }
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        with pytest.raises(ConfigError) as exc_info:
            Config(str(config_path))
        assert "Invalid log level" in str(exc_info.value)


def test_config_validation_missing_db_path(tmp_path):
    """Test that Config provides a default db_path if not specified."""
    config_data = {
        "users": {"testuser": {"quota": {"daily": 60}}},
        # Intentionally omit db_path
    }

    config_path = tmp_path / "missing_db.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config_data, f)

    # db_path should be provided by default config, not raise an error
    config = Config(str(config_path))
    assert config["db_path"] is not None
    assert "guardian.sqlite" in config["db_path"]


def test_config_validation_invalid_time_format():
    """Test that invalid reset_time format raises ConfigError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "invalid_time.yaml"
        config_data = {
            "db_path": "/tmp/test.db",
            "ipc_socket": "/tmp/test.sock",
            "logging": {"level": "INFO"},
            "reset_time": "25:00",  # Invalid hour
        }
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        with pytest.raises(ConfigError) as exc_info:
            Config(str(config_path))
        assert "invalid time format" in str(exc_info.value).lower()


def test_config_validation_invalid_quota_type():
    """Test that invalid quota type raises ConfigError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "invalid_quota.yaml"
        config_data = {
            "db_path": "/tmp/test.db",
            "ipc_socket": "/tmp/test.sock",
            "logging": {"level": "INFO"},
            "users": {"testuser": {"quota": "not_a_dict"}},  # Should be a dict
        }
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        with pytest.raises(ConfigError) as exc_info:
            Config(str(config_path))
        assert "quota" in str(exc_info.value).lower()


def test_config_validation_negative_quota():
    """Test that negative quota raises ConfigError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "negative_quota.yaml"
        config_data = {
            "db_path": "/tmp/test.db",
            "ipc_socket": "/tmp/test.sock",
            "logging": {"level": "INFO"},
            "users": {"testuser": {"quota": {"daily": -10}}},  # Negative quota
        }
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        with pytest.raises(ConfigError) as exc_info:
            Config(str(config_path))
        assert (
            "non-negative" in str(exc_info.value).lower()
            or "positive" in str(exc_info.value).lower()
        )


def test_config_validation_invalid_curfew_time():
    """Test that invalid curfew time raises ConfigError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "invalid_curfew.yaml"
        config_data = {
            "db_path": "/tmp/test.db",
            "ipc_socket": "/tmp/test.sock",
            "logging": {"level": "INFO"},
            "users": {
                "testuser": {
                    "curfew": {
                        "weekday": {"start": "99:99", "end": "20:00"}  # Invalid time
                    }
                }
            },
        }
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        with pytest.raises(ConfigError) as exc_info:
            Config(str(config_path))
        assert "time format" in str(exc_info.value).lower()


def test_config_dict_access():
    """Test dictionary-style access to config."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "dict_access.yaml"
        config_data = {
            "db_path": "/tmp/test.db",
            "ipc_socket": "/tmp/test.sock",
            "logging": {"level": "INFO"},
            "custom_key": "custom_value",
        }
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        config = Config(str(config_path))
        # Test __getitem__
        assert config["custom_key"] == "custom_value"

        # Test get with default
        assert config.get("nonexistent", "default") == "default"


def test_config_merge_deep():
    """Test that nested configs merge correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "merge_test.yaml"
        config_data = {
            "db_path": "/tmp/test.db",
            "ipc_socket": "/tmp/test.sock",
            "logging": {"level": "DEBUG", "format": "json"},  # Override default
            "users": {
                "user1": {"quota": {"daily": 60}},
                "user2": {"quota": {"daily": 120}},
            },
        }
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        config = Config(str(config_path))
        # Should have merged logging config
        assert config["logging"]["level"] == "DEBUG"
        # Should have both users
        assert "user1" in config["users"]
        assert "user2" in config["users"]


def test_config_validation_invalid_grace_minutes():
    """Test that invalid grace_minutes raises ConfigError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "invalid_grace.yaml"
        config_data = {
            "db_path": "/tmp/test.db",
            "ipc_socket": "/tmp/test.sock",
            "logging": {"level": "INFO"},
            "users": {"testuser": {"grace_minutes": "not_an_integer"}},
        }
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        with pytest.raises(ConfigError) as exc_info:
            Config(str(config_path))
        assert "must be an integer" in str(exc_info.value).lower()
