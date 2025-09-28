"""
Configuration management for the Guardian Daemon.
"""

import os

import yaml

from guardian_daemon.logging import get_logger

logger = get_logger("Config")


class ConfigError(Exception):
    """Custom exception for configuration errors."""

    pass


class Config:
    """
    Handles loading, merging, and validating the daemon's configuration.
    """

    def __init__(self, config_dir=None):
        if config_dir is None:
            config_dir = os.path.join(os.path.dirname(__file__), "..")

        default_path = os.path.join(config_dir, "default-config.yaml")
        user_path = os.path.join(config_dir, "config.yaml")

        self.data = self._load_config(default_path)
        user_config = self._load_config(user_path)

        if user_config:
            self._merge_configs(self.data, user_config)

        self._validate_config()
        logger.info("Configuration loaded and validated successfully.")

    def _load_config(self, path):
        """Loads a YAML configuration file."""
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r") as f:
                return yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            logger.error(f"Error parsing YAML file at {path}: {e}")
            raise ConfigError(f"Could not parse {path}") from e
        except IOError as e:
            logger.error(f"Error reading file at {path}: {e}")
            raise ConfigError(f"Could not read {path}") from e

    def _merge_configs(self, base, override):
        """Recursively merges the override config into the base config."""
        for key, value in override.items():
            if isinstance(value, dict) and key in base and isinstance(base[key], dict):
                self._merge_configs(base[key], value)
            else:
                base[key] = value

    def _validate_config(self):
        """
        Validates the loaded configuration against a basic schema.
        Raises ConfigError on validation failure.
        """
        if not isinstance(self.data.get("logging"), dict):
            raise ConfigError("'logging' section is missing or not a dictionary.")
        if not isinstance(self.data.get("db_path"), str):
            raise ConfigError("'db_path' is missing or not a string.")
        if not isinstance(self.data.get("ipc_socket"), str):
            raise ConfigError("'ipc_socket' is missing or not a string.")

        users = self.data.get("users")
        if users and not isinstance(users, dict):
            raise ConfigError("'users' section must be a dictionary.")

        logger.debug("Configuration validation passed.")

    def get(self, key, default=None):
        """Gets a configuration value."""
        return self.data.get(key, default)

    def __getitem__(self, key):
        """Allows dictionary-style access to config data."""
        return self.data[key]
