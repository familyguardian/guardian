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

    def __init__(self, config_path=None):
        self.config_path = None
        self.data = {}

        # Step 1: First locate the default configuration file
        install_dir = os.path.join(os.path.dirname(__file__), "..")
        default_path = os.path.join(install_dir, "default-config.yaml")

        if os.path.exists(default_path):
            # Always start with the default configuration
            self.data = self._load_config(default_path)
            logger.debug(f"Loaded default configuration from: {default_path}")
        else:
            logger.warning(f"Default config not found at: {default_path}")
            # We continue without a default config, but this might lead to validation errors

        # Step 2: Determine which user config to load
        user_config_path = None

        # First priority: Explicitly provided path
        if config_path and os.path.exists(config_path):
            user_config_path = config_path
            logger.debug(f"Using explicitly provided config path: {config_path}")

        # Second priority: Environment variable
        elif os.environ.get("GUARDIAN_DAEMON_CONFIG") and os.path.exists(
            os.environ.get("GUARDIAN_DAEMON_CONFIG")
        ):
            user_config_path = os.environ.get("GUARDIAN_DAEMON_CONFIG")
            logger.debug(f"Using config path from environment: {user_config_path}")

        # Third priority: Persistent system path
        elif os.path.exists("/etc/guardian/daemon/config.yaml"):
            user_config_path = "/etc/guardian/daemon/config.yaml"
            logger.debug(f"Using persistent system config path: {user_config_path}")

        # Fourth priority: Local config (development)
        elif os.path.exists(os.path.join(install_dir, "config.yaml")):
            user_config_path = os.path.join(install_dir, "config.yaml")
            logger.debug(f"Using local config path: {user_config_path}")

        # Step 3: Merge user config into default config if found
        if user_config_path:
            user_config = self._load_config(user_config_path)
            if user_config:
                self._merge_configs(self.data, user_config)
                self.config_path = user_config_path
                logger.info(f"Merged user configuration from: {user_config_path}")
            else:
                logger.warning(
                    f"User config at {user_config_path} was empty or invalid"
                )
        else:
            logger.info("No user configuration found, using default configuration only")
            self.config_path = default_path

        # Validate the final merged configuration
        try:
            self._validate_config()
            logger.info("Final configuration validated successfully")
        except ConfigError as e:
            logger.error(f"Configuration validation failed: {e}")
            raise

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
