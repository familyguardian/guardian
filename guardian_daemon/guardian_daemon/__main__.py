import asyncio
import hashlib
import os

import yaml

from guardian_daemon.logging import get_logger
from guardian_daemon.pam_manager import PamManager
from guardian_daemon.policy import Policy
from guardian_daemon.sessions import SessionTracker
from guardian_daemon.storage import Storage
from guardian_daemon.systemd_manager import SystemdManager

logger = get_logger("GuardianDaemon")


class GuardianDaemon:
    """
    Main class of the Guardian Daemon.
    Initializes all core components and controls the flow.
    """

    def __init__(self):
        """
        Initializes Policy, Storage, PAM, Systemd, and SessionTracker.
        Loads default-config.yaml first, then config.yaml and overwrites values.
        """
        # Lade Default-Konfiguration
        with open(
            os.path.join(os.path.dirname(__file__), "../default-config.yaml"), "r"
        ) as f:
            config = yaml.safe_load(f)
        # Überschreibe mit Werten aus config.yaml, falls vorhanden
        config_path = os.path.join(os.path.dirname(__file__), "../config.yaml")
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                user_config = yaml.safe_load(f)
            if user_config:
                config.update(user_config)
        db_path = config.get("db_path", "guardian.sqlite")
        self.policy = Policy()
        self.storage = Storage(db_path)
        self.pam = PamManager(self.policy)
        self.systemd = SystemdManager()
        self.tracker = SessionTracker(self.policy, config)
        self.last_config = self._get_config_snapshot()

    def _get_config_snapshot(self):
        """
        Generates a snapshot of the current configuration.

        Returns:
            str: SHA256 hash of the policy data
        """
        return hashlib.sha256(yaml.dump(self.policy.data).encode()).hexdigest()

    async def periodic_reload(self):
        """
        Checks every 5 minutes for config changes and updates timers/PAM rules.
        """
        while True:
            await asyncio.sleep(300)
            old_snapshot = self.last_config
            self.policy.reload()
            new_snapshot = self._get_config_snapshot()
            if new_snapshot != old_snapshot:
                logger.info("Config changed, updating timers and PAM rules.")
                self.pam = PamManager(self.policy)
                self.pam.write_time_rules()
                reset_time = self.policy.data.get("reset_time", "03:00")
                self.systemd.create_daily_reset_timer(reset_time)
                # Curfew timer update (example: use start/end from policy)
                curfew = self.policy.data.get("curfew", {})
                start_time = curfew.get("start", "22:00")
                end_time = curfew.get("end", "06:00")
                self.systemd.create_curfew_timer(start_time, end_time)
                self.systemd.reload_systemd()
                self.last_config = new_snapshot

    def check_and_recover_reset(self):
        """
        Checks at startup whether the last reset was executed and recovers it if necessary.
        """
        # TODO: Implement logic, e.g. with timestamp in storage or lockfile
        logger.info("Check if last reset needs to be recovered (Stub).")

    async def run(self):
        """
        Starts all components and tasks of the daemon.
        """
        self.pam.write_time_rules()
        reset_time = self.policy.data.get("reset_time", "03:00")
        self.systemd.create_daily_reset_timer(reset_time)
        # Curfew timer setup (example: use start/end from policy)
        curfew = self.policy.data.get("curfew", {})
        start_time = curfew.get("start", "22:00")
        end_time = curfew.get("end", "06:00")
        self.systemd.create_curfew_timer(start_time, end_time)
        self.systemd.reload_systemd()
        self.check_and_recover_reset()
        await asyncio.gather(self.tracker.run(), self.periodic_reload())


def main():
    """
    Entry Point für den Guardian-Daemon.
    """
    daemon = GuardianDaemon()
    asyncio.run(daemon.run())


if __name__ == "__main__":
    main()

# Entry point for guardian-daemon (systemd service)
