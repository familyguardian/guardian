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

    Configuration Loading Priority (highest to lowest):
    1. Explicitly provided path via config_path parameter
    2. Environment variable GUARDIAN_DAEMON_CONFIG
    3. System-wide config at /etc/guardian/daemon/config.yaml
    4. Local config (development) in project directory

    The configuration system uses a two-layer approach:
    - Default configuration (always loaded from default-config.yaml)
    - User configuration (merged on top of defaults)

    This ensures that all required keys exist even if the user
    config is minimal or missing.

    Example:
        >>> config = Config("/etc/guardian/daemon/config.yaml")
        >>> db_path = config.get("db_path")
        >>> users = config["users"]
    """

    def __init__(self, config_path=None):
        """
        Initialize the configuration system.

        Args:
            config_path: Optional explicit path to configuration file.
                        If not provided, searches in priority order:
                        1. GUARDIAN_DAEMON_CONFIG env var
                        2. /etc/guardian/daemon/config.yaml
                        3. Local config.yaml (development)

        Raises:
            ConfigError: If configuration validation fails
        """
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
        """
        Loads a YAML configuration file.

        Args:
            path: Path to the YAML file

        Returns:
            dict: Parsed configuration data, or empty dict if file doesn't exist

        Raises:
            ConfigError: If YAML parsing fails or file cannot be read
        """
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
        """
        Recursively merges the override config into the base config.

        Args:
            base: Base configuration dictionary (modified in place)
            override: Configuration dictionary to merge in (takes priority)

        Note:
            Dictionaries are merged recursively, all other types override.
        """
        for key, value in override.items():
            if isinstance(value, dict) and key in base and isinstance(base[key], dict):
                self._merge_configs(base[key], value)
            else:
                base[key] = value

    @staticmethod
    def _validate_time_format(time_str: str, field_name: str) -> None:
        """Validate HH:MM time format."""
        import re

        if not isinstance(time_str, str):
            raise ConfigError(f"'{field_name}' must be a string")
        if not re.match(r"^(2[0-3]|[01]?[0-9]):([0-5][0-9])$", time_str):
            raise ConfigError(
                f"'{field_name}' has invalid time format: '{time_str}'. Expected HH:MM (00:00-23:59)"
            )

    @staticmethod
    def _validate_positive_integer(
        value: any, field_name: str, allow_zero: bool = False
    ) -> None:
        """Validate that value is a positive integer."""
        if not isinstance(value, int):
            raise ConfigError(
                f"'{field_name}' must be an integer, got {type(value).__name__}"
            )
        if allow_zero:
            if value < 0:
                raise ConfigError(f"'{field_name}' must be non-negative, got {value}")
        else:
            if value <= 0:
                raise ConfigError(f"'{field_name}' must be positive, got {value}")

    def _validate_config(self):
        """
        Validates the loaded configuration against a comprehensive schema.
        Raises ConfigError on validation failure with specific error messages.
        """
        # Validate logging section
        if not isinstance(self.data.get("logging"), dict):
            raise ConfigError("'logging' section is missing or not a dictionary.")

        logging_cfg = self.data.get("logging", {})
        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if "level" in logging_cfg:
            if logging_cfg["level"] not in valid_log_levels:
                raise ConfigError(
                    f"Invalid log level: '{logging_cfg['level']}'. "
                    f"Must be one of: {', '.join(valid_log_levels)}"
                )

        # Validate paths
        if not isinstance(self.data.get("db_path"), str):
            raise ConfigError("'db_path' is missing or not a string.")
        if not isinstance(self.data.get("ipc_socket"), str):
            raise ConfigError("'ipc_socket' is missing or not a string.")

        # Validate reset_time format
        if "reset_time" in self.data:
            self._validate_time_format(self.data["reset_time"], "reset_time")

        # Validate users section
        users = self.data.get("users")
        if users is not None:
            if not isinstance(users, dict):
                raise ConfigError("'users' section must be a dictionary.")

            # Validate each user's configuration
            for username, user_config in users.items():
                if not isinstance(user_config, dict):
                    raise ConfigError(
                        f"Configuration for user '{username}' must be a dictionary."
                    )

                # Validate quota if present
                if "quota" in user_config:
                    quota = user_config["quota"]
                    if not isinstance(quota, dict):
                        raise ConfigError(f"'{username}.quota' must be a dictionary.")

                    if "daily" in quota:
                        self._validate_positive_integer(
                            quota["daily"], f"{username}.quota.daily", allow_zero=True
                        )
                    if "weekly" in quota:
                        self._validate_positive_integer(
                            quota["weekly"], f"{username}.quota.weekly", allow_zero=True
                        )

                # Validate curfew if present
                if "curfew" in user_config:
                    curfew = user_config["curfew"]
                    if not isinstance(curfew, dict):
                        raise ConfigError(f"'{username}.curfew' must be a dictionary.")

                    # Validate curfew time windows
                    for period in ["weekday", "weekend"]:
                        if period in curfew:
                            period_cfg = curfew[period]
                            if not isinstance(period_cfg, dict):
                                raise ConfigError(
                                    f"'{username}.curfew.{period}' must be a dictionary."
                                )

                            if "start" in period_cfg:
                                self._validate_time_format(
                                    period_cfg["start"],
                                    f"{username}.curfew.{period}.start",
                                )
                            if "end" in period_cfg:
                                self._validate_time_format(
                                    period_cfg["end"], f"{username}.curfew.{period}.end"
                                )

                # Validate grace_minutes if present
                if "grace_minutes" in user_config:
                    self._validate_positive_integer(
                        user_config["grace_minutes"], f"{username}.grace_minutes"
                    )

        logger.debug("Configuration validation passed.")

    def get(self, key, default=None):
        """
        Gets a configuration value.

        Args:
            key: Configuration key to retrieve
            default: Default value if key not found

        Returns:
            The configuration value or default
        """
        return self.data.get(key, default)

    def __getitem__(self, key):
        """
        Allows dictionary-style access to config data.

        Args:
            key: Configuration key to retrieve

        Returns:
            The configuration value

        Raises:
            KeyError: If key doesn't exist
        """
        return self.data[key]
