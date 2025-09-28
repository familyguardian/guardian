"""
User manager for guardian-daemon.
Manages login time windows for children via /etc/security/time.conf
and handles user-specific systemd services.
"""

import grp
import os
import pwd
import shutil
import subprocess
import time
from pathlib import Path

from guardian_daemon.logging import get_logger
from guardian_daemon.policy import Policy

logger = get_logger("UserManager")

TIME_CONF_PATH = Path("/etc/security/time.conf")
# Assumes the script is run from the project's structure, giving us the root.
PROJECT_ROOT = Path(__file__).parent.parent.parent
SOURCE_SERVICE_FILE = PROJECT_ROOT / "systemd_units" / "user" / "guardian_agent.service"


def chown_recursive(path, uid, gid):
    path = Path(path)
    if path.exists():
        shutil.chown(path, user=uid, group=gid)
        if path.is_dir():
            for sub in path.iterdir():
                chown_recursive(sub, uid, gid)


class UserManager:
    def ensure_kids_group(self):
        """
        Ensure the 'kids' group exists and all managed users are members of it.
        """
        group_name = "kids"
        users = set(self.policy.data.get("users", {}).keys())

        # Check if group exists, create if not
        try:
            grp.getgrnam(group_name)
            logger.debug(f"Group '{group_name}' already exists.")
        except KeyError:
            logger.info(f"Creating group '{group_name}'.")
            try:
                subprocess.run(
                    ["groupadd", group_name], check=True, capture_output=True, text=True
                )
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to create group '{group_name}': {e.stderr}")
                return

        # Add each user to the group
        for username in users:
            try:
                pwd.getpwnam(username)
            except KeyError:
                logger.warning(f"User '{username}' does not exist on system.")
                continue

            try:
                user_groups = [
                    g.gr_name for g in grp.getgrall() if username in g.gr_mem
                ]
                # Also get the primary group
                uid = pwd.getpwnam(username).pw_uid
                primary_group = grp.getgrgid(uid).gr_name
                user_groups.append(primary_group)
            except Exception as e:
                logger.error(f"Could not determine groups for user {username}: {e}")
                continue

            if group_name not in user_groups:
                logger.info(f"Adding user '{username}' to group '{group_name}'.")
                try:
                    subprocess.run(
                        ["usermod", "-aG", group_name, username],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                except subprocess.CalledProcessError as e:
                    logger.error(
                        f"Failed to add user '{username}' to group '{group_name}': {e.stderr}"
                    )
            else:
                logger.debug(f"User '{username}' is already in group '{group_name}'.")

    def ensure_pam_time_module(self):
        """
        Ensure that the pam_time.so module is properly configured in PAM services.
        """
        PAM_SERVICES = ["sddm", "login", "system-auth"]

        for service in PAM_SERVICES:
            service_file = Path(f"/etc/pam.d/{service}")
            if not service_file.exists():
                continue

            try:
                with open(service_file, "r") as f:
                    content = f.read()

                if "pam_time.so" not in content:
                    # Make a backup of the original file
                    backup_file = service_file.with_suffix(f".bak-{int(time.time())}")
                    shutil.copy(service_file, backup_file)

                    # Find the account section and add pam_time.so
                    lines = content.split("\n")
                    account_section = [
                        i
                        for i, line in enumerate(lines)
                        if line.strip().startswith("account")
                        and not line.strip().startswith("#")
                    ]

                    if account_section:
                        # Insert after the last account line
                        insert_pos = max(account_section) + 1
                        lines.insert(insert_pos, "account    required    pam_time.so")

                        # Write the updated file
                        with open(service_file, "w") as f:
                            f.write("\n".join(lines))

                        logger.info(f"Added pam_time.so module to {service_file}")
                    else:
                        logger.warning(
                            f"Could not find account section in {service_file}"
                        )
            except Exception as e:
                logger.error(f"Failed to update PAM configuration for {service}: {e}")

        logger.info("PAM time module configuration checked")

    def setup_dbus_policy(self):
        """
        Creates /etc/dbus-1/system.d/guardian.conf to allow group 'kids' access to org.guardian.Daemon.
        """
        policy_path = Path("/etc/dbus-1/system.d/guardian.conf")
        policy_xml = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
        "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
        <busconfig>
            <!-- Consolidated policy for the root user (the daemon) -->
            <policy user="root">
                <allow own="org.guardian.Daemon"/>
                <allow send_destination_prefix="org.guardian.Agent"/>
                <allow receive_sender_prefix="org.guardian.Agent"/>
            </policy>

            <!-- Policy for managed users -->
            <policy group="kids">
                <allow own_prefix="org.guardian.Agent"/>
                <allow send_destination="org.guardian.Daemon"/>
                <allow receive_user="root"/>
            </policy>

            <!-- Default policy for everyone else -->
            <policy context="default">
                <allow send_destination="org.guardian.Daemon"/>
            </policy>
        </busconfig>
        """.strip()
        try:
            with open(policy_path, "w") as f:
                f.write(policy_xml)
            logger.info(
                f"D-Bus policy file written to {policy_path} for group 'kids' and user 'root'."
            )
            # Reload D-Bus to apply the new policy immediately
            try:
                subprocess.run(["systemctl", "reload", "dbus.service"], check=True)
                logger.info("Reloaded D-Bus system service to apply new policy.")
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                logger.error(f"Failed to reload D-Bus service: {e}")
        except Exception as e:
            logger.error(f"Failed to write D-Bus policy file: {e}")

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
        # Ensure the PAM time module is loaded in the relevant PAM services
        self.ensure_pam_time_module()

        rules = self._generate_rules()
        managed_usernames = set(self.policy.data.get("users", {}).keys())

        new_lines = ["# Managed by guardian-daemon"]
        if TIME_CONF_PATH.exists():
            with open(TIME_CONF_PATH, "r") as f:
                lines = f.readlines()

            for line in lines:
                line = line.strip()
                if not line or line.startswith("# Managed by guardian-daemon"):
                    continue

                try:
                    parts = line.strip().split(";")
                    # Check if the rule is for a user we manage
                    if len(parts) >= 3 and parts[2] in managed_usernames:
                        logger.debug(f"Removing old rule for managed user: {parts[2]}")
                        continue
                except Exception:
                    # Keep lines that don't parse correctly
                    pass

                new_lines.append(line)
        else:
            logger.debug("No existing time.conf found, starting fresh.")

        new_lines.extend(rules)

        try:
            with open(TIME_CONF_PATH, "w") as f:
                for line in new_lines:
                    f.write(line + "\n")
            os.chmod(TIME_CONF_PATH, 0o644)
            logger.info(f"Wrote {len(rules)} managed rules to {TIME_CONF_PATH}")
        except Exception as e:
            logger.error(f"Failed to write to {TIME_CONF_PATH}: {e}")

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
                    # Allow login during allowed hours, for multiple services
                    for service in ["login", "sddm", "gdm", "lightdm", "xdm", "kde"]:
                        rules.append(f"{service};*;{username};{day};{times}")
        logger.debug(f"Generated {len(rules)} PAM time rules.")
        return rules

    def remove_time_rules(self):
        """
        Remove time rules set by guardian-daemon from /etc/security/time.conf.
        """
        if TIME_CONF_PATH.exists():
            logger.info("Removing managed time rules from time.conf.")
            try:
                with open(TIME_CONF_PATH, "r") as f:
                    lines = f.readlines()

                new_lines = []
                for line in lines:
                    line = line.strip()
                    if not line or line.startswith("# Managed by guardian-daemon"):
                        continue

                    is_managed = False
                    try:
                        parts = line.strip().split(";")
                        if len(parts) >= 3 and parts[2] in self.policy.data.get(
                            "users", {}
                        ):
                            is_managed = True
                    except Exception:
                        pass

                    if not is_managed:
                        new_lines.append(line)

                with open(TIME_CONF_PATH, "w") as f:
                    for line in new_lines:
                        f.write(line + "\n")
            except Exception as e:
                logger.error(f"Failed to remove time rules from {TIME_CONF_PATH}: {e}")

    def setup_user_service(self, username: str):
        """
        Sets up the guardian_agent.service for the given user's systemd.
        Updates the service file if its checksum has changed.
        """
        try:
            user_info = pwd.getpwnam(username)
            user_home = Path(user_info.pw_dir)
            user_systemd_path = user_home / ".config/systemd/user"
            service_file_path = user_systemd_path / "guardian_agent.service"

            user_systemd_path.mkdir(parents=True, exist_ok=True)
            # Ownership will be set recursively below

            if not SOURCE_SERVICE_FILE.exists():
                logger.error(
                    f"Source service file {SOURCE_SERVICE_FILE} does not exist."
                )
                return

            shutil.copy(SOURCE_SERVICE_FILE, service_file_path)
            # Ownership will be set recursively below

            # Reload, enable, and start the service for the user
            self._run_systemctl_user_command(username, "daemon-reload")
            self._run_systemctl_user_command(
                username, "enable", "guardian_agent.service"
            )
            self._run_systemctl_user_command(
                username, "start", "guardian_agent.service"
            )

            # Recursively set ownership for all files and directories in ~/.config
            chown_recursive(user_home / ".config", user_info.pw_uid, user_info.pw_gid)

        except KeyError:
            logger.error(f"User '{username}' not found, cannot setup service.")
        except Exception as e:
            logger.error(f"Failed to setup user service for {username}: {e}")

    def ensure_systemd_user_service(self, username):
        """
        Ensure that systemd user services are set up for the given user without enabling lingering.
        """
        try:
            user_info = pwd.getpwnam(username)
            user_home = Path(user_info.pw_dir)
            service_file = user_home / ".config/systemd/user/guardian_agent.service"

            if not service_file.exists():
                self.setup_user_service(username)

            # Check if the guardian_agent service is active, if not, start it
            result = self._run_systemctl_user_command(
                username, "is-active", "guardian_agent.service"
            )
            if result and result.stdout.strip() != "active":
                logger.debug(f"Starting guardian_agent service for user {username}.")
                self._run_systemctl_user_command(
                    username, "start", "guardian_agent.service"
                )
            else:
                logger.debug(
                    f"guardian_agent service for user {username} is already active."
                )

        except KeyError:
            logger.error(f"User '{username}' not found, cannot ensure systemd service.")
        except Exception as e:
            logger.error(f"Failed to ensure systemd user service for {username}: {e}")

    def _run_systemctl_user_command(self, username, *args):
        """Helper to run systemctl --user commands for a given user."""
        try:
            command = [
                "runuser",
                "-l",
                username,
                "-c",
                f"systemctl --user {' '.join(args)}",
            ]
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            return result
        except subprocess.CalledProcessError as e:
            logger.error(
                f"Error running systemctl command for {username} ('{' '.join(args)}'): {e.stderr}"
            )
            return None
        except FileNotFoundError:
            logger.error(
                "`runuser` command not found. Is the systemd package installed?"
            )
            return None
