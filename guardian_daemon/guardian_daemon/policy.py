"""
Policy loader for guardian-daemon.
Loads and validates settings from a YAML configuration file.
"""

from typing import Any, Dict, Optional

from guardian_daemon.config import Config
from guardian_daemon.logging import get_logger
from guardian_daemon.storage import Storage

logger = get_logger("Policy")


class Policy:

    def __init__(
        self, config_path: Optional[str] = None, db_path: Optional[str] = None
    ):
        """
        Initializes the Policy instance.
        Uses the Config class to read configuration and synchronizes it with the database.

        Args:
            config_path (str, optional): Path to the YAML configuration file.
            db_path (str, optional): Path to the SQLite database.
        """
        # Use the Config class to load configuration
        config = Config(config_path)
        self.data = config.data
        self.config_path = config.config_path

        logger.info("Policy loaded from configuration")

        # Use provided db_path or get from config
        self.db_path = db_path or self.data.get(
            "db_path", "/var/lib/guardian/guardian.sqlite"
        )

        logger.debug(f"Using database path: {self.db_path}")
        self.storage = Storage(self.db_path)
        self.storage.sync_config_to_db(self.data)
        logger.info("Policy loaded and synchronized to DB")

    def get_user_policy(self, username: str) -> Optional[Dict[str, Any]]:
        """
        Return the policy settings for a specific user.

        Args:
            username (str): Username

        Returns:
            dict | None: The user's settings or None if not present.
        """
        logger.debug(f"Fetching policy for user: {username}")
        return self.storage.get_user_settings(username)

    def get_default(self, key: str) -> Any:
        """
        Return a default value from the policy.

        Args:
            key (str): Name of the default key

        Returns:
            Any: The default value or None
        """
        defaults = self.storage.get_user_settings("default")
        logger.debug(f"Fetching default policy value for key: {key}")
        if defaults:
            return defaults.get(key)
        return None

    def get_timezone(self) -> str:
        """
        Return the configured timezone.

        Returns:
            str: Timezone (e.g. "Europe/Berlin")
        """
        tz = self.data.get("timezone", "Europe/Berlin")
        logger.debug(f"Configured timezone: {tz}")
        return tz

    def reload(self):
        """
        Reload the policy configuration and synchronize with the database.
        """

        logger.info("Reloading policy from configuration")
        config = Config(self.config_path)
        self.data = config.data
        self.storage.sync_config_to_db(self.data)
        logger.info("Policy reloaded and synchronized to DB")


# Example usage
if __name__ == "__main__":
    policy = Policy("config.yaml")
    logger.info(f"Timezone: {policy.get_timezone()}")
    logger.info(f"Default Quota: {policy.get_default('daily_quota_minutes')}")
    logger.info(f"Policy for kid1: {policy.get_user_policy('kid1')}")
# Policy models (pydantic)
