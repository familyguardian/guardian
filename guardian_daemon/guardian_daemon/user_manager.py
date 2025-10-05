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
    def user_exists(self, username):
        """
        Check if a user exists on the system.
        Returns True if user exists, False otherwise.
        """
        try:
            pwd.getpwnam(username)
            return True
        except KeyError:
            return False

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
            if not self.user_exists(username):
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
        Ensures pam_time.so is active by creating and selecting a custom authselect profile.
        This method is designed to be safe and idempotent.
        """
        if not shutil.which("authselect"):
            logger.error("authselect command not found. This system is not supported.")
            return

        guardian_profile_name = "guardian"
        custom_profile_path = Path(f"/etc/authselect/custom/{guardian_profile_name}")

        try:
            # 1. Determine the current base profile and its features
            current_profile_name = "local"  # Default to 'local'
            current_features = []
            try:
                result = subprocess.run(
                    ["authselect", "current"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                lines = result.stdout.strip().split("\n")
                # Profile ID: custom/guardian or Profile ID: local
                profile_line = lines[0]
                if "custom/" in profile_line:
                    # If we are already on a custom profile, treat its base as the current one
                    # This is a simplification; we'll base our profile on a known-good one.
                    # For now, we'll stick to 'local' as the safe base.
                    pass
                else:
                    current_profile_name = profile_line.split(":")[1].strip()

                # Extract features
                in_features_section = False
                for line in lines[1:]:
                    if "Aktivierte Funktionen:" in line or "Enabled features:" in line:
                        in_features_section = True
                        continue
                    if in_features_section and line.strip().startswith("-"):
                        feature = line.strip().lstrip("-").strip()
                        if feature:
                            current_features.append(feature)
                logger.info(
                    f"Detected base profile '{current_profile_name}' with features: {current_features}"
                )

            except Exception as e:
                logger.warning(
                    f"Could not reliably determine current authselect profile, defaulting to 'local'. Error: {e}"
                )

            # 2. Create the custom 'guardian' profile if it doesn't exist.
            if not custom_profile_path.exists():
                logger.info(
                    f"Creating new custom authselect profile '{guardian_profile_name}' based on '{current_profile_name}'."
                )
                subprocess.run(
                    [
                        "authselect",
                        "create-profile",
                        guardian_profile_name,
                        "-b",
                        current_profile_name,
                        "--symlink-meta",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )

            # 3. Add pam_time.so to the account stack in the custom profile's system-auth.
            # We only need to modify system-auth, as password-auth typically includes it.
            system_auth_path = custom_profile_path / "system-auth"
            if not system_auth_path.exists():
                logger.error(f"Custom profile is broken. Missing {system_auth_path}")
                return

            with open(system_auth_path, "r+") as f:
                lines = f.readlines()
                # Check if our line is already present
                if any("pam_time.so" in line for line in lines):
                    logger.debug(
                        "pam_time.so is already in the custom profile's system-auth."
                    )
                else:
                    # Find the last 'account' line and insert after it
                    last_account_line_index = -1
                    for i, line in enumerate(lines):
                        if line.strip().startswith("account"):
                            last_account_line_index = i

                    if last_account_line_index != -1:
                        lines.insert(
                            last_account_line_index + 1,
                            "account     required      pam_time.so\n",
                        )
                        f.seek(0)
                        f.writelines(lines)
                        f.truncate()
                        logger.info(
                            "Added pam_time.so to custom profile's system-auth."
                        )
                    else:
                        logger.error(
                            "Could not find 'account' section in custom profile's system-auth."
                        )
                        return

            # 4. Select the custom profile with all original features.
            logger.info(
                f"Selecting 'custom/{guardian_profile_name}' profile with features."
            )
            select_cmd = [
                "authselect",
                "select",
                f"custom/{guardian_profile_name}",
            ] + current_features
            result = subprocess.run(
                select_cmd, check=False, capture_output=True, text=True
            )

            if result.returncode != 0:
                # If selection fails, try with --force. This is sometimes needed.
                logger.warning(
                    "Initial authselect command failed, retrying with --force."
                )
                select_cmd.append("--force")
                subprocess.run(select_cmd, check=True, capture_output=True, text=True)

            logger.info(
                "Successfully selected and applied the custom guardian authselect profile."
            )

        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.error(
                f"Failed to configure authselect: {e.stderr if hasattr(e, 'stderr') else e}"
            )
            logger.error("PAM time restrictions will NOT be active.")
        except Exception as e:
            logger.error(
                f"An unexpected error occurred during authselect configuration: {e}"
            )
            logger.error("PAM time restrictions will NOT be active.")

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

            # Reload PAM configuration if the system supports it
            try:
                # Check if systemd-reload-pam exists
                result = subprocess.run(
                    ["which", "systemd-reload-pam"],
                    check=False,
                    capture_output=True,
                    text=True,
                )

                if result.returncode == 0:
                    subprocess.run(["systemd-reload-pam"], check=True)
                    logger.info("Reloaded PAM configuration")
            except Exception as e:
                logger.warning(f"Could not reload PAM configuration: {e}")

        except Exception as e:
            logger.error(f"Failed to write to {TIME_CONF_PATH}: {e}")

    def _generate_rules(self):
        """
        Generates the PAM time rules from the policy.
        The default behavior of pam_time.so is to deny access if no rule matches.
        Therefore, we must create a rule to explicitly ALLOW non-managed users.
        """
        rules = []
        users = self.policy.data.get("users", {})
        managed_users = list(users.keys())

        # Rule 1: Allow all users who are NOT in the 'kids' group at all times.
        # The '!@group' syntax refers to users not in a local group.
        # This is much more robust than listing individual users.
        if managed_users:
            rules.append("*;*;!@kids;Al0000-2400")
            rules.append("# --- Guardian Managed Rules Below ---")

        # Rule 2: Define specific time restrictions for each managed user.
        day_mapping = {
            "weekdays": "Wk",
            "saturday": "Sa",
            "sunday": "Su",
            "all": "Al",
        }

        for username in managed_users:
            user_policy = self.policy.get_user_policy(username)
            curfew = user_policy.get("curfew", self.policy.get_default("curfew"))

            if curfew:
                # Create a combined day specification for all allowed times.
                # Example: Wk0800-2000&Sa0900-2200&Su0900-2000
                time_specs = []
                for day, time_range in curfew.items():
                    day_code = day_mapping.get(day)
                    if day_code:
                        # Convert "08:00-20:00" to "0800-2000"
                        start, end = time_range.split("-")
                        start = start.replace(":", "")
                        end = end.replace(":", "")
                        time_specs.append(f"{day_code}{start}-{end}")

                if time_specs:
                    # Apply the combined rule to all services and ttys for the user.
                    combined_times = "&".join(time_specs)
                    rules.append(f"*;*;{username};{combined_times}")

        logger.debug(f"Generated {len(rules)} PAM time rules.")
        return rules

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
        Ensures correct directory structure and permissions.
        """
        try:
            user_info = pwd.getpwnam(username)
            user_home = Path(user_info.pw_dir)
            config_path = user_home / ".config"
            user_systemd_path = config_path / "systemd/user"
            service_file_path = user_systemd_path / "guardian_agent.service"

            # Check if source service file exists
            if not SOURCE_SERVICE_FILE.exists():
                logger.error(
                    f"Source service file {SOURCE_SERVICE_FILE} does not exist."
                )
                return

            # Check if user's home directory exists and is accessible
            if not user_home.exists():
                logger.error(
                    f"Home directory for user '{username}' does not exist: {user_home}"
                )
                return

            # Create directory structure with proper permissions at each step
            try:
                # Create directory structure step by step to ensure correct ownership
                # First create .config if needed and set permissions
                if not config_path.exists():
                    logger.debug(f"Creating .config directory for {username}")
                    config_path.mkdir(mode=0o755, exist_ok=True)
                    os.chown(config_path, user_info.pw_uid, user_info.pw_gid)

                # Create systemd directory and set ownership
                systemd_path = config_path / "systemd"
                if not systemd_path.exists():
                    logger.debug(f"Creating systemd directory for {username}")
                    systemd_path.mkdir(mode=0o755, exist_ok=True)
                    os.chown(systemd_path, user_info.pw_uid, user_info.pw_gid)

                # Create user directory and set ownership
                if not user_systemd_path.exists():
                    logger.debug(f"Creating systemd/user directory for {username}")
                    user_systemd_path.mkdir(mode=0o755, exist_ok=True)
                    os.chown(user_systemd_path, user_info.pw_uid, user_info.pw_gid)

                # Fix ownership of any existing directories that might have wrong permissions
                for path in [config_path, systemd_path, user_systemd_path]:
                    if path.exists() and (
                        path.stat().st_uid != user_info.pw_uid
                        or path.stat().st_gid != user_info.pw_gid
                    ):
                        logger.debug(f"Fixing ownership of {path} for user {username}")
                        os.chown(path, user_info.pw_uid, user_info.pw_gid)

                # Copy the service file and set permissions
                shutil.copy(SOURCE_SERVICE_FILE, service_file_path)
                os.chown(service_file_path, user_info.pw_uid, user_info.pw_gid)
                os.chmod(service_file_path, 0o644)  # rw-r--r--

                logger.info(
                    f"Successfully created guardian agent service file for {username}"
                )
            except PermissionError as e:
                logger.error(
                    f"Permission error setting up directories for {username}: {e}"
                )
                return

            # Reload, enable, and start the service for the user if they're logged in
            # First check if user has active sessions
            try:
                result = subprocess.run(
                    ["loginctl", "show-user", username, "--property=State"],
                    check=True,
                    capture_output=True,
                    text=True,
                )

                is_active = "State=active" in result.stdout

                if is_active:
                    logger.info(
                        f"User {username} is active, setting up systemd service"
                    )
                    self._run_systemctl_user_command(username, "daemon-reload")
                    self._run_systemctl_user_command(
                        username, "enable", "guardian_agent.service"
                    )
                    self._run_systemctl_user_command(
                        username, "start", "guardian_agent.service"
                    )
                else:
                    logger.info(
                        f"User {username} is not logged in, service will be enabled at next login"
                    )
                    # For non-logged-in users, we leave the service file in place but don't try to enable/start
            except Exception as e:
                logger.warning(f"Could not determine login status for {username}: {e}")

        except KeyError:
            logger.error(f"User '{username}' not found, cannot setup service.")
        except Exception as e:
            logger.error(f"Failed to setup user service for {username}: {e}")

    def ensure_systemd_user_service(self, username):
        """
        Ensure that systemd user services are set up for the given user without enabling lingering.
        Only starts the service if the user is actively logged in with a session.
        """
        try:
            # First check if user exists in the system
            user_info = pwd.getpwnam(username)
            user_home = Path(user_info.pw_dir)

            # Check if user is actively logged in (has active sessions)
            is_logged_in = False
            try:
                # Use loginctl to check if user has active sessions
                result = subprocess.run(
                    ["loginctl", "show-user", username, "--property=State"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                if "State=active" in result.stdout:
                    is_logged_in = True
                    logger.debug(f"User {username} is logged in with active session")
            except Exception as e:
                logger.warning(f"Could not determine login status for {username}: {e}")

            # Only proceed with service setup/start if user is logged in
            if is_logged_in:
                service_dir = user_home / ".config/systemd/user"
                service_file = service_dir / "guardian_agent.service"

                # Make sure the directory exists with correct permissions
                if not service_dir.exists():
                    logger.debug(f"Creating systemd user directory for {username}")
                    service_dir.mkdir(parents=True, exist_ok=True)
                    # Set correct ownership
                    chown_recursive(service_dir, user_info.pw_uid, user_info.pw_gid)

                # Set up the service if it doesn't exist
                if not service_file.exists():
                    logger.info(
                        f"Setting up guardian agent service for logged-in user {username}"
                    )
                    self.setup_user_service(username)

                # Now check if the service is active and start if needed
                result = self._run_systemctl_user_command(
                    username, "is-active", "guardian_agent.service"
                )
                if result and result.stdout.strip() != "active":
                    logger.debug(f"Starting guardian_agent service for user {username}")
                    self._run_systemctl_user_command(
                        username, "start", "guardian_agent.service"
                    )
                else:
                    logger.debug(
                        f"guardian_agent service for user {username} is already active"
                    )
            else:
                logger.info(
                    f"User {username} is not logged in, skipping agent service start"
                )
                # Just ensure the service file exists for next login
                service_dir = user_home / ".config/systemd/user"
                if service_dir.exists():
                    service_file = service_dir / "guardian_agent.service"
                    if not service_file.exists() and SOURCE_SERVICE_FILE.exists():
                        logger.debug(
                            f"Preparing service file for future login of {username}"
                        )
                        service_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copy(SOURCE_SERVICE_FILE, service_file)
                        chown_recursive(service_dir, user_info.pw_uid, user_info.pw_gid)

        except KeyError:
            logger.error(f"User '{username}' not found, cannot ensure systemd service.")
        except Exception as e:
            logger.error(f"Failed to ensure systemd user service for {username}: {e}")

    def _run_systemctl_user_command(self, username, *args):
        """Helper to run systemctl --user commands for a given user.

        Uses runuser to execute commands as the target user with minimal environment variables.
        Avoids using login shell (-l) to prevent issues with profile scripts (like Nobara's).
        Handles common error cases and logs appropriate messages.
        """
        try:
            # Get user info for XDG_RUNTIME_DIR environment variable
            user_info = pwd.getpwnam(username)
            uid = user_info.pw_uid

            # Set up environment variables directly for subprocess instead of exporting them in shell
            clean_env = {
                "XDG_RUNTIME_DIR": f"/run/user/{uid}",
                "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{uid}/bus",
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",  # Minimal PATH
                "HOME": user_info.pw_dir,
                "USER": username,
            }

            # Build the command to run systemctl directly, without using a shell
            command = [
                "runuser",
                # No -l flag to avoid loading profile scripts
                username,
                "-c",
                f"systemctl --user {' '.join(args)}",
            ]

            # Run the command with a reasonable timeout and clean environment
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,  # Add timeout to avoid hanging
                env=clean_env,  # Pass clean environment variables
            )
            return result

        except subprocess.CalledProcessError as e:
            # Handle specific error cases
            stderr = e.stderr.strip() if hasattr(e, "stderr") else ""
            if "Failed to connect to bus" in stderr:
                logger.warning(f"User {username} doesn't have an active session bus")
            elif "Unit guardian_agent.service not found" in stderr:
                logger.warning(f"Service file not properly loaded for user {username}")
            else:
                logger.error(
                    f"Error running systemctl command for {username} ('{' '.join(args)}'): {stderr}"
                )
            return None

        except subprocess.TimeoutExpired:
            logger.error(
                f"Command timed out for user {username}: 'systemctl --user {' '.join(args)}'"
            )
            return None

        except FileNotFoundError:
            logger.error(
                "`runuser` command not found. Is the systemd package installed?"
            )
            return None

        except Exception as e:
            logger.error(f"Unexpected error running command for {username}: {e}")
            return None
