import asyncio
import datetime
import hashlib
import time

import yaml

from guardian_daemon.config import Config, ConfigError
from guardian_daemon.enforcer import Enforcer
from guardian_daemon.ipc import GuardianIPCServer
from guardian_daemon.logging import get_logger, setup_logging
from guardian_daemon.policy import Policy
from guardian_daemon.sessions import SessionTracker
from guardian_daemon.storage import Storage
from guardian_daemon.systemd_manager import SystemdManager
from guardian_daemon.user_manager import UserManager

logger = get_logger("GuardianDaemon")


class GuardianDaemon:
    """
    Main class of the Guardian Daemon.
    Initializes all core components and controls the flow.
    """

    def __init__(self, config: Config):
        """
        Initializes Policy, Storage, UserManager, Systemd, SessionTracker, and Enforcer.
        """
        self.config = config
        db_path = self.config.get("db_path", "guardian.sqlite")

        self.policy = Policy()
        self.storage = Storage(db_path)
        self.usermanager = UserManager(self.policy)
        self.systemd = SystemdManager()
        self.tracker = SessionTracker(self.policy, self.config, self.usermanager)
        self.enforcer = Enforcer(self.policy, self.tracker)
        self.ipc_server = GuardianIPCServer(self.config, self.tracker, self.policy)
        self.last_config_hash = self._get_config_hash()

    def _get_config_hash(self):
        """
        Generates a hash of the current configuration data.

        Returns:
            str: SHA256 hash of the policy data
        """
        return hashlib.sha256(yaml.dump(self.policy.data).encode()).hexdigest()

    async def periodic_reload(self):
        """
        Checks every 5 minutes for config changes and updates timers/UserManager rules.
        """
        while True:
            await asyncio.sleep(300)
            old_hash = self.last_config_hash
            self.policy.reload()
            new_hash = self._get_config_hash()
            if new_hash != old_hash:
                logger.info("Config changed, updating timers and UserManager rules.")
                self.usermanager = UserManager(self.policy)
                self.usermanager.write_time_rules()
                reset_time = self.policy.data.get("reset_time", "03:00")
                self.systemd.create_daily_reset_timer(reset_time)
                # Curfew timer update (example: use start/end from policy)
                curfew = self.policy.data.get("curfew", {})
                start_time = curfew.get("start", "22:00")
                end_time = curfew.get("end", "06:00")
                self.systemd.create_curfew_timer(start_time, end_time)
                await self.systemd.reload_systemd()
                self.last_config_hash = new_hash

    async def enforce_users(self):
        """
        Periodically enforce policies for all users.
        """
        while True:
            await asyncio.sleep(60)  # Check every minute
            # Create a copy of active users to avoid issues with concurrent modification
            active_users = list(await self.tracker.get_active_users())
            for username in active_users:
                # Only enforce once per user - the enforcer method shouldn't be called twice
                # The enforce_user method will handle the notifications
                await self.enforcer.enforce_user(username)

    async def check_and_recover_reset(self):
        """
        Checks at startup whether the last reset was executed and recovers it if necessary.
        """
        # Use EPOCH timestamp in storage to track last reset
        last_reset = self.storage.get_last_reset_timestamp()
        today = datetime.date.today()
        reset_time_str = self.policy.data.get("reset_time", "03:00")
        reset_hour, reset_minute = map(int, reset_time_str.split(":"))
        scheduled_reset = datetime.datetime.combine(
            today, datetime.time(reset_hour, reset_minute)
        )
        scheduled_reset_epoch = scheduled_reset.timestamp()
        now_epoch = time.time()
        # If last reset was before today's scheduled reset and now is after scheduled reset, recover
        if last_reset is None or (
            last_reset < scheduled_reset_epoch and now_epoch > scheduled_reset_epoch
        ):
            logger.info("Missed daily reset detected. Performing recovery.")
            await self.tracker.perform_daily_reset()
            self.storage.set_last_reset_timestamp(now_epoch)
        else:
            logger.info("Daily reset already performed or not needed.")

    async def run(self):
        """
        Starts all components and tasks of the daemon.
        """
        self.usermanager.write_time_rules()
        self.usermanager.ensure_kids_group()
        self.usermanager.setup_dbus_policy()

        # Only set up services for users that actually exist on the system
        for username in self.policy.data.get("users", {}):
            if self.usermanager.user_exists(username):
                self.usermanager.setup_user_service(username)
            # We don't log here as ensure_kids_group already logs non-existent users

        reset_time = self.policy.data.get("reset_time", "03:00")
        self.systemd.create_daily_reset_timer(reset_time)
        # Curfew timer setup (example: use start/end from policy)
        curfew = self.policy.data.get("curfew", {})
        start_time = curfew.get("start", "22:00")
        end_time = curfew.get("end", "09:00")
        self.systemd.create_curfew_timer(start_time, end_time)
        await self.systemd.reload_systemd()
        await self.check_and_recover_reset()
        await asyncio.gather(
            self.tracker.run(),
            self.periodic_reload(),
            self.enforce_users(),
            self.ipc_server.start(),
        )


def main():
    """
    Entry Point for the Guardian-Daemon.
    """
    try:
        config = Config()
        setup_logging(config.data)  # Setup logging once with the loaded config
        daemon = GuardianDaemon(config)
        asyncio.run(daemon.run())
    except ConfigError as e:
        # Use a basic logger if config fails, as structlog might not be configured.
        import logging

        logging.basicConfig()
        log = logging.getLogger("GuardianDaemon")
        log.error(f"Configuration error: {e}")
    except Exception as e:
        import logging

        logging.basicConfig()
        log = logging.getLogger("GuardianDaemon")
        log.error(f"An unexpected error occurred: {e}", exc_info=True)


if __name__ == "__main__":
    main()

# Entry point for guardian-daemon (systemd service)
