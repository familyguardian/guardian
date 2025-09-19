"""
Policy loader for guardian-daemon.
Loads and validates settings from a YAML configuration file.
"""

from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from guardian_daemon.storage import Storage


class Policy:

    def __init__(
        self, config_path: Optional[str] = None, db_path: Optional[str] = None
    ):
        """
        Initializes the Policy instance.
        Reads the configuration from a YAML file and synchronizes it with the database.

        Args:
            config_path (str, optional): Path to the YAML configuration file.
            db_path (str, optional): Path to the SQLite database.
        """
        import os

        env_path = os.environ.get("GUARDIAN_DAEMON_CONFIG")
        self.config_path = Path(config_path or env_path or "config.yaml")

        with open(self.config_path, "r") as f:
            self.data = yaml.safe_load(f)
        self.db_path = db_path or self.data.get(
            "db_path", "/var/lib/guardian/guardian.sqlite"
        )
        self.storage = Storage(self.db_path)
        # Sync config to DB beim Start
        self.storage.sync_config_to_db(self.data)

    def get_user_policy(self, username: str) -> Optional[Dict[str, Any]]:
        """
        Return the policy settings for a specific user.

        Args:
            username (str): Username

        Returns:
            dict | None: The user's settings or None if not present.
        """
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
        if defaults:
            return defaults.get(key)
        return None

    def get_timezone(self) -> str:
        """
        Return the configured timezone.

        Returns:
            str: Timezone (e.g. "Europe/Berlin")
        """
        return self.data.get("timezone", "Europe/Berlin")

    def reload(self):
        """
        Reload the policy configuration and synchronize with the database.
        """

        with open(self.config_path, "r") as f:
            self.data = yaml.safe_load(f)
        self.storage.sync_config_to_db(self.data)


# Example usage
if __name__ == "__main__":
    policy = Policy("config.yaml")
    print("Timezone:", policy.get_timezone())
    print("Default Quota:", policy.get_default("daily_quota_minutes"))
    print("Policy for kid1:", policy.get_user_policy("kid1"))
# Policy models (pydantic)
