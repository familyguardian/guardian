"""
User manager for guardian-daemon.
Manages login time windows for children via /etc/security/time.conf
and handles user-specific systemd services.
"""

import os
from pathlib import Path

from guardian_daemon.logging import get_logger
from guardian_daemon.policy import Policy

logger = get_logger("UserManager")

TIME_CONF_PATH = Path("/etc/security/time.conf")


class UserManager:
    """
    Manages user-specific configurations, including PAM time rules and systemd services.
    """

    def __init__(self, policy: Policy):
        """
        Initialize the UserManager with a policy instance.
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

    def setup_user_service(self, username: str):
        """
        Sets up the guardian_agent.service for the given user's systemd.
        Updates the service file if its checksum has changed.
        """
        import hashlib

        user_systemd_path = Path(f"/home/{username}/.config/systemd/user")
        service_file_path = user_systemd_path / "guardian_agent.service"

        # Ensure the systemd user directory exists
        user_systemd_path.mkdir(parents=True, exist_ok=True)

        # Compute checksum of the source service file
        source_service_file = Path(
            "/usr/local/guardian/systemd_units/user/guardian_agent.service"
        )
        if not source_service_file.exists():
            logger.error(f"Source service file {source_service_file} does not exist.")
            return

        with open(source_service_file, "rb") as src:
            source_checksum = hashlib.sha256(src.read()).hexdigest()

        # Check if the destination file exists and compare checksums
        if service_file_path.exists():
            with open(service_file_path, "rb") as dest:
                dest_checksum = hashlib.sha256(dest.read()).hexdigest()
            if source_checksum == dest_checksum:
                logger.debug(f"Service file for {username} is up-to-date.")
                return

        # Copy the updated service file
        logger.debug(f"Updating service file for {username} at {service_file_path}")
        with (
            open(source_service_file, "r") as src,
            open(service_file_path, "w") as dest,
        ):
            dest.write(src.read())

        # Reload, enable, and start the service for the user
        os.system(f"runuser -l {username} -c 'systemctl --user daemon-reload'")
        os.system(
            f"runuser -l {username} -c 'systemctl --user enable guardian_agent.service'"
        )
        os.system(
            f"runuser -l {username} -c 'systemctl --user start guardian_agent.service'"
        )

    def ensure_systemd_user_service(self, username):
        """
        Ensure that systemd user services are set up for the given user without enabling lingering.
        """
        try:
            # Check if systemd user directory exists
            user_systemd_path = os.path.expanduser(f"~{username}/.config/systemd/user")
            if not os.path.exists(user_systemd_path):
                os.makedirs(user_systemd_path)

            # Check if the guardian_agent.service file exists
            service_file = os.path.join(user_systemd_path, "guardian_agent.service")
            if not os.path.exists(service_file):
                self.setup_user_service(username)

            # Check if the guardian_agent service is active, if not, start it
            status_cmd = f"runuser -l {username} -c 'systemctl --user is-active guardian_agent.service'"
            status = os.popen(status_cmd).read().strip()
            if status != "active":
                logger.debug(f"Starting guardian_agent service for user {username}.")
                os.system(
                    f"runuser -l {username} -c 'systemctl --user start guardian_agent.service'"
                )
            else:
                logger.debug(
                    f"guardian_agent service for user {username} is already active."
                )

            logger.debug(f"Systemd user service directory ensured for user {username}.")
        except Exception as e:
            logger.error(f"Failed to ensure systemd user service for {username}: {e}")
