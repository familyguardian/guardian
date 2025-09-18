"""
PAM-Manager für guardian-daemon
Verwaltet Login-Zeitfenster für Kinder über /etc/security/time.conf
"""

import os
from pathlib import Path

from guardian_daemon.policy import Policy

TIME_CONF_PATH = Path("/etc/security/time.conf")


class PamManager:
    def __init__(self, policy: Policy):
        self.policy = policy

    def write_time_rules(self):
        """
        Aktualisiert die Zeitregeln für alle Kinder gemäß Policy in /etc/security/time.conf,
        ohne fremde Regeln zu überschreiben.
        """
        rules = self._generate_rules()
        managed_usernames = set(self.policy.data.get("users", {}).keys())
        # Backup der bestehenden Datei
        if TIME_CONF_PATH.exists():
            os.rename(TIME_CONF_PATH, TIME_CONF_PATH.with_suffix(".conf.bak"))
            with open(TIME_CONF_PATH.with_suffix(".conf.bak"), "r") as f:
                lines = f.readlines()
            # Filter: Alle Zeilen behalten, die nicht zu den guardian-Kindern gehören
            new_lines = []
            for line in lines:
                if line.startswith("login;*"):
                    # Extrahiere den Usernamen
                    parts = line.strip().split(";")
                    if len(parts) >= 3 and parts[2] in managed_usernames:
                        continue  # Zeile wird durch guardian-daemon ersetzt
                new_lines.append(line.rstrip("\n"))
        else:
            new_lines = []
        # Schreibe die neue Datei
        with open(TIME_CONF_PATH, "w") as f:
            f.write("# Managed by guardian-daemon\n")
            for line in new_lines:
                if line:
                    f.write(line + "\n")
            for rule in rules:
                f.write(rule + "\n")

    def _generate_rules(self):
        """
        Erzeugt die PAM-Zeitregeln aus der Policy
        """
        rules = []
        users = self.policy.data.get("users", {})
        for username, user_policy in users.items():
            curfew = user_policy.get("curfew", self.policy.get_default("curfew"))
            # Beispiel: weekdays: "08:00-20:00"
            if curfew:
                for day, times in curfew.items():
                    # PAM time.conf Syntax: <service>;<ttys>;<users>;<day>;<start>-<end>
                    rules.append(f"login;*;{username};{day};{times}")
        return rules

    def remove_time_rules(self):
        """
        Entfernt die von guardian-daemon gesetzten Zeitregeln
        """
        if TIME_CONF_PATH.exists():
            with open(TIME_CONF_PATH, "r") as f:
                lines = f.readlines()
            with open(TIME_CONF_PATH, "w") as f:
                for line in lines:
                    if not line.startswith("login;*") and not line.startswith(
                        "# Managed by guardian-daemon"
                    ):
                        f.write(line)


# PAM time.conf block management
