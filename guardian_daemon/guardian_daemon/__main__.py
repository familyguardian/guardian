import asyncio
import datetime
import hashlib
import os
import time

import yaml

from guardian_daemon.enforcer import Enforcer
from guardian_daemon.logging import get_logger
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

    def __init__(self):
        """
        Initializes Policy, Storage, UserManager, Systemd, SessionTracker, and Enforcer.
        Loads default-config.yaml first, then config.yaml and overwrites values.
        """
        # Load Default Configuration
        with open(
            os.path.join(os.path.dirname(__file__), "../default-config.yaml"), "r"
        ) as f:
            config = yaml.safe_load(f)
        # Overwrite with values from config.yaml, if available
        config_path = os.path.join(os.path.dirname(__file__), "../config.yaml")
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                user_config = yaml.safe_load(f)
            if user_config:
                config.update(user_config)
        db_path = config.get("db_path", "guardian.sqlite")
        self.policy = Policy()
        self.storage = Storage(db_path)
        self.usermanager = UserManager(self.policy)
        self.systemd = SystemdManager()
        self.tracker = SessionTracker(self.policy, config)
        self.enforcer = Enforcer(self.policy, self.tracker)
        self.last_config = self._get_config_snapshot()
        self.agent_paths = {}  # username -> dbus object path
        self.agent_path_retry = 5  # seconds between retries
        self.agent_path_attempts = 6  # total attempts

    async def find_agent_path(self, username):
        """
        Poll for the agent D-Bus object path for a given username.
        Returns the object path if found, else None.
        """
        import asyncio

        from dbus_next.aio import MessageBus
        from dbus_next.constants import BusType

        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        # Try /org/guardian/Agent, /org/guardian/Agent2, ... up to Agent10
        for attempt in range(self.agent_path_attempts):
            for n in range(1, 11):
                if n == 1:
                    obj_path = "/org/guardian/Agent"
                else:
                    obj_path = f"/org/guardian/Agent{n}"
                try:
                    proxy = await bus.introspect("org.guardian.Agent", obj_path)
                    iface = bus.get_proxy_object(
                        "org.guardian.Agent", obj_path, proxy
                    ).get_interface("org.guardian.Agent")
                    agent_username = await iface.call_get_username()
                    if agent_username == username:
                        self.agent_paths[username] = obj_path
                        return obj_path
                except Exception:
                    continue
            await asyncio.sleep(self.agent_path_retry)
        return None

    def _get_config_snapshot(self):
        """
        Generates a snapshot of the current configuration.

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
            old_snapshot = self.last_config
            self.policy.reload()
            new_snapshot = self._get_config_snapshot()
            if new_snapshot != old_snapshot:
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
                self.systemd.reload_systemd()
                self.last_config = new_snapshot

    async def enforce_users(self):
        """
        Periodically enforce policies for all users.
        """
        while True:
            await asyncio.sleep(60)  # Check every minute
            active_users = self.tracker.get_active_users()
            for username in active_users:
                # Check if agent path is known, else try to find it
                if username not in self.agent_paths:
                    path = await self.find_agent_path(username)
                    if path:
                        logger.info(f"[DAEMON] Found agent for {username} at {path}")
                    else:
                        logger.warning(f"[DAEMON] Could not find agent for {username}")
                # Now you can send notifications via D-Bus if needed
                self.enforcer.enforce_user(username)

    def check_and_recover_reset(self):
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
            self.tracker.perform_daily_reset()
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
        reset_time = self.policy.data.get("reset_time", "03:00")
        self.systemd.create_daily_reset_timer(reset_time)
        # Curfew timer setup (example: use start/end from policy)
        curfew = self.policy.data.get("curfew", {})
        start_time = curfew.get("start", "22:00")
        end_time = curfew.get("end", "09:00")
        self.systemd.create_curfew_timer(start_time, end_time)
        self.systemd.reload_systemd()
        self.check_and_recover_reset()
        await asyncio.gather(
            self.tracker.run(), self.periodic_reload(), self.enforce_users()
        )


def main():
    """
    Entry Point for the Guardian-Daemon.
    """
    daemon = GuardianDaemon()
    asyncio.run(daemon.run())


if __name__ == "__main__":
    main()

# Entry point for guardian-daemon (systemd service)
