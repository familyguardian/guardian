"""
Policy loader for guardian-daemon.
Loads and validates settings from a YAML configuration file.
"""

import datetime
from typing import Any, Dict, Optional

from guardian_daemon.config import Config
from guardian_daemon.logging import get_logger
from guardian_daemon.storage import Storage

logger = get_logger("Policy")

# Map datetime.weekday() (Monday=0 .. Sunday=6) to the curfew keys to try, in
# priority order. "all" applies to every day and is the fallback used when no
# day-specific window is configured.
_CURFEW_DAY_KEYS: dict[int, tuple[str, ...]] = {
    0: ("weekdays", "all"),
    1: ("weekdays", "all"),
    2: ("weekdays", "all"),
    3: ("weekdays", "all"),
    4: ("weekdays", "all"),
    5: ("saturday", "all"),
    6: ("sunday", "all"),
}


def parse_hhmm(value: str) -> datetime.time:
    """Parse an ``"HH:MM"`` string into a :class:`datetime.time`."""
    hour, minute = map(int, value.split(":"))
    return datetime.time(hour, minute)


def is_within_curfew_window(
    current: datetime.time, start: datetime.time, end: datetime.time
) -> bool:
    """Return True if ``current`` falls inside the allowed login window.

    Curfew windows describe the time during which login is *allowed*. The window
    is half-open ``[start, end)``. A window whose start is later than its end
    (e.g. ``22:00``-``06:00``) wraps past midnight, mirroring the
    split-at-midnight rules emitted for ``pam_time.so``.
    """
    if start > end:
        return current >= start or current < end
    return start <= current < end


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

    def has_quota(self, username: str) -> bool:
        """Check if a user has quota settings and is not quota_exempt."""
        # Check both config and database for quota settings
        user_settings_config = self.data.get("users", {}).get(username, {})
        user_settings_db = self.storage.get_user_settings(username)

        # Check if user is quota exempt (from config or merged db settings)
        if user_settings_config.get("quota_exempt", False):
            return False
        if user_settings_db and user_settings_db.get("quota_exempt", False):
            return False

        # Support both formats:
        # New format: "quota" key exists
        # Old format: "daily_quota_minutes" key exists
        if user_settings_config and "quota" in user_settings_config:
            return True
        if user_settings_db:
            if "quota" in user_settings_db or "daily_quota_minutes" in user_settings_db:
                return True

        return False

    def has_curfew(self, username: str) -> bool:
        """Check if a user has curfew settings."""
        user_settings = self.data.get("users", {}).get(username, {})
        return "curfew" in user_settings

    def get_user_quota(self, username: str) -> tuple[int, int]:
        """Get daily and weekly quota for a user."""
        if username not in self.data.get("users", {}):
            raise KeyError(f"User {username} not found in policy")

        # Get user settings from database (which has the actual values)
        user_settings = self.storage.get_user_settings(username)
        if not user_settings:
            # Fallback to config data
            user_settings = self.data["users"][username]

        # Support both formats:
        # 1. New format: {"quota": {"daily": 90, "weekly": 0}}
        # 2. Old format: {"daily_quota_minutes": 90}
        if "quota" in user_settings:
            quota = user_settings["quota"]
            daily = quota.get("daily", 0)
            weekly = quota.get("weekly", 0)
        else:
            # Old format or direct keys
            daily = user_settings.get("daily_quota_minutes", 0)
            weekly = user_settings.get("weekly_quota_minutes", 0)

        return daily, weekly

    def get_effective_curfew(self, username: str) -> Optional[dict]:
        """Return the effective curfew for a user as a per-day mapping.

        Merges the user's own curfew over the configured defaults -- the same
        resolution :meth:`UserManager._generate_rules` uses to emit ``pam_time``
        rules -- so the Python-level curfew checks agree with what PAM actually
        enforces. Returns ``None`` when neither the user nor the defaults define
        a curfew.
        """
        user_policy = self.get_user_policy(username)
        if not user_policy:
            return None
        curfew = user_policy.get("curfew")
        if curfew is None:
            curfew = self.get_default("curfew")
        return curfew if isinstance(curfew, dict) and curfew else None

    def get_user_curfew(self, username: str, weekday: int) -> Optional[dict[str, str]]:
        """Return the allowed-login window for ``username`` on a given weekday.

        The window reflects the *effective* curfew (the user's own settings
        merged over the configured defaults), i.e. the same windows
        :meth:`UserManager._generate_rules` emits, so a user that relies on the
        default curfew is reported as restricted here too.

        Args:
            username: The user to look up.
            weekday: Day of week as :meth:`datetime.date.weekday`
                (Monday=0 .. Sunday=6).

        Returns:
            ``{"start": "HH:MM", "end": "HH:MM"}`` describing the window during
            which login is allowed, or ``None`` if no curfew applies that day.
            Curfew is stored as per-day ``"HH:MM-HH:MM"`` ranges keyed by
            ``weekdays``/``saturday``/``sunday``/``all``.
        """
        curfew = self.get_effective_curfew(username)
        if not curfew:
            return None

        window = None
        for key in _CURFEW_DAY_KEYS.get(weekday, ("all",)):
            if curfew.get(key):
                window = curfew[key]
                break

        if not isinstance(window, str) or "-" not in window:
            return None
        start, end = window.split("-", 1)
        return {"start": start.strip(), "end": end.strip()}

    def get_monitored_users(self) -> list[str]:
        """Get list of all monitored users (excluding those with monitored: False)."""
        result = []
        for username, settings in self.data.get("users", {}).items():
            # Check if user is explicitly marked as not monitored
            if not settings.get("monitored", True):  # Default is True if not specified
                continue
            # Include user if they have quota or curfew settings
            if self.has_quota(username) or self.has_curfew(username):
                result.append(username)
        return result

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
        Returns the configured timezone or the default timezone.

        Returns:
            str: Timezone string
        """
        tz = self.data.get("timezone", "Europe/Berlin")
        logger.debug(f"Configured timezone: {tz}")
        return tz

    def get_all_usernames(self) -> list:
        """
        Returns a list of all usernames in the policy.

        Returns:
            list: List of usernames
        """
        users = list(self.data.get("users", {}).keys())
        logger.debug(f"Retrieved {len(users)} users from policy")
        return users

    def get_grace_time(self, username: str) -> int:
        """
        Returns the grace time in minutes for a user.
        This is the time allowed after quota is exhausted before terminating the session.

        Args:
            username (str): Username to get grace time for

        Returns:
            int: Grace time in minutes (defaults to 5)
        """
        user_policy = self.get_user_policy(username)
        if user_policy and "grace_minutes" in user_policy:
            return user_policy["grace_minutes"]

        # Fall back to defaults if no specific user setting
        defaults = self.storage.get_user_settings("default")
        if defaults and "grace_minutes" in defaults:
            return defaults["grace_minutes"]

        # Use hardcoded default if nothing else is available
        return 5  # Default grace period: 5 minutes

    def add_user(self, username: str) -> bool:
        """
        Adds a user to the policy with default settings.

        Args:
            username (str): Username to add

        Returns:
            bool: True if the user was added, False otherwise
        """
        if not username:
            logger.error("Cannot add user with empty username")
            return False

        if username in self.data.get("users", {}):
            logger.debug(f"User '{username}' already exists in policy")
            return True

        # Ensure users dict exists
        if "users" not in self.data:
            self.data["users"] = {}

        # Add user with empty config (will use defaults)
        self.data["users"][username] = {}
        logger.info(f"Added user '{username}' to policy with default settings")

        # Update database
        self.storage.sync_config_to_db(self.data)
        logger.info(f"User '{username}' synchronized to database")
        return True

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
