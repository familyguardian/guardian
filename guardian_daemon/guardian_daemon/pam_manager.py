"""
PAM manager for guardian-daemon.
Manages login time windows for children via /etc/security/time.conf
"""

import os
from pathlib import Path

from guardian_daemon.logging import get_logger
from guardian_daemon.policy import Policy

logger = get_logger("PamManager")

TIME_CONF_PATH = Path("/etc/security/time.conf")


class PamManager:
    """
    Manages PAM time.conf rules for login time windows according to policy.
    """

    def __init__(self, policy: Policy):
        """
        Initialize the PamManager with a policy instance.
        """
        self.policy = policy

    def write_time_rules(self):
        """
        Updates the time rules for all children according to the policy in /etc/security/time.conf,
        without overwriting foreign rules.
        """
        rules = self._generate_rules()
        managed_usernames = set(self.policy.data.get("users", {}).keys())
        # Backup der bestehenden Datei
        if TIME_CONF_PATH.exists():
            logger.debug(
                f"Backing up existing time.conf to {TIME_CONF_PATH.with_suffix('.conf.bak')}"
            )
            os.rename(TIME_CONF_PATH, TIME_CONF_PATH.with_suffix(".conf.bak"))
            with open(TIME_CONF_PATH.with_suffix(".conf.bak"), "r") as f:
                lines = f.readlines()
            new_lines = []
            for line in lines:
                if line.startswith("login;*"):
                    parts = line.strip().split(";")
                    if len(parts) >= 3 and parts[2] in managed_usernames:
                        logger.debug(f"Replacing rule for managed user: {parts[2]}")
                        continue
                new_lines.append(line.rstrip("\n"))
        else:
            logger.debug("No existing time.conf found, starting fresh.")
            new_lines = []
        # Schreibe die neue Datei
        with open(TIME_CONF_PATH, "w") as f:
            logger.info(f"Writing updated time.conf with {len(rules)} managed rules.")
            f.write("# Managed by guardian-daemon\n")
            for line in new_lines:
                if line:
                    f.write(line + "\n")
            for rule in rules:
                logger.debug(f"Adding rule: {rule}")
                f.write(rule + "\n")

    def _generate_rules(self):
        """
        Generates the PAM time rules from the policy
        """
        rules = []
        users = self.policy.data.get("users", {})
        for username, user_policy in users.items():
            curfew = user_policy.get("curfew", self.policy.get_default("curfew"))
            # Beispiel: weekdays: "08:00-20:00"
            if curfew:
                for day, times in curfew.items():
                    # PAM time.conf syntax: <service>;<ttys>;<users>;<day>;<start>-<end>
                    rules.append(f"login;*;{username};{day};{times}")
        logger.debug(f"Generated {len(rules)} PAM time rules.")
        return rules

    def remove_time_rules(self):
        """
        Remove time rules set by guardian-daemon from /etc/security/time.conf.
        """
        if TIME_CONF_PATH.exists():
            logger.info("Removing managed time rules from time.conf.")
            with open(TIME_CONF_PATH, "r") as f:
                lines = f.readlines()
            with open(TIME_CONF_PATH, "w") as f:
                for line in lines:
                    if not line.startswith("login;*") and not line.startswith(
                        "# Managed by guardian-daemon"
                    ):
                        f.write(line)


# PAM time.conf block management
