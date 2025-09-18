import asyncio
import logging
import os

import yaml

from guardian_daemon.pam_manager import PamManager
from guardian_daemon.policy import Policy
from guardian_daemon.sessions import SessionTracker
from guardian_daemon.storage import Storage
from guardian_daemon.systemd_manager import SystemdManager

logging.basicConfig(
    level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s"
)


class GuardianDaemon:
    """
    Hauptklasse des Guardian-Daemon.
    Initialisiert alle Kernkomponenten und steuert den Ablauf.
    """

    def __init__(self):
        """
        Initialisiert Policy, Storage, PAM, Systemd und SessionTracker.
        Lädt zuerst default-config.yaml, dann config.yaml und überschreibt Werte.
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
        Erzeuge einen Hash/Snapshot der aktuellen Policy-Konfiguration.
        Returns:
            str: SHA256-Hash der Policy-Daten
        """
        import hashlib

        import yaml

        return hashlib.sha256(yaml.dump(self.policy.data).encode()).hexdigest()

    async def periodic_reload(self):
        """
        Prüft alle 5 Minuten auf Config-Änderungen und aktualisiert Timer/PAM.
        """
        while True:
            await asyncio.sleep(300)
            old_snapshot = self.last_config
            self.policy.reload()
            new_snapshot = self._get_config_snapshot()
            if new_snapshot != old_snapshot:
                logging.info("Config geändert, aktualisiere Timer und PAM-Regeln.")
                self.pam = PamManager(self.policy)
                self.pam.write_time_rules()
                reset_time = self.policy.data.get("reset_time", "03:00")
                self.systemd.create_daily_reset_timer(reset_time)
                self.last_config = new_snapshot

    def check_and_recover_reset(self):
        """
        Prüft beim Start, ob der letzte Reset ausgeführt wurde, und holt ihn ggf. nach.
        """
        # TODO: Implementiere Logik, z.B. mit Timestamp in Storage oder Lockfile
        logging.info("Prüfe, ob Tagesreset nachgeholt werden muss (Stub).")

    async def run(self):
        """
        Startet alle Komponenten und Tasks des Daemons.
        """
        self.pam.write_time_rules()
        reset_time = self.policy.data.get("reset_time", "03:00")
        self.systemd.create_daily_reset_timer(reset_time)
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
