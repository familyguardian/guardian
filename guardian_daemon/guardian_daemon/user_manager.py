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
from typing import TYPE_CHECKING

from guardian_daemon.logging import get_logger
from guardian_daemon.policy import Policy

if TYPE_CHECKING:
    from guardian_daemon.sessions import SessionTracker

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
    """
    Manages user-specific configurations, PAM time rules, and systemd services.
    
    This class is responsible for:
    - Managing the 'kids' group and user memberships
    - Writing and maintaining PAM time.conf rules for curfews
    - Setting up user-specific systemd services (guardian-agent)
    - Configuring D-Bus policies for agent communication
    - Ensuring PAM modules are properly configured
    
    The UserManager works closely with the Policy class to enforce
    time-based access controls and user quotas.
    
    Security: All methods that accept usernames validate them against path traversal
    and use canonical system paths via pwd.getpwnam().
    """
    
    @staticmethod
    def validate_username(username: str) -> bool:
        """
        Validate username format to prevent path traversal and injection attacks.
        
        Args:
            username: The username to validate
            
        Returns:
            bool: True if username is valid, False otherwise
        """
        import re
        # Only allow alphanumeric characters, underscore, and hyphen
        # This prevents path traversal (../) and other injection attempts
        if not username or not isinstance(username, str):
            return False
        return bool(re.match(r'^[a-zA-Z0-9_-]+$', username))
    
    def user_exists(self, username):
        """
        Check if a user exists on the system.
        
        Args:
            username: The username to check
            
        Returns:
            bool: True if user exists, False otherwise
        """
        if not self.validate_username(username):
            return False
        try:
            pwd.getpwnam(username)
            return True
        except KeyError:
            return False

    def ensure_kids_group(self):
        """
        Ensure the 'kids' group exists and all managed users are members of it.
        Also ensures all managed users are in the 'users' group to access agent files.
        """
        required_groups = ["kids", "users"]
        managed_users = set(self.policy.data.get("users", {}).keys())

        # Check if 'kids' group exists, create if not
        try:
            grp.getgrnam("kids")
            logger.debug("Group 'kids' already exists.")
        except KeyError:
            logger.info("Creating group 'kids'.")
            try:
                subprocess.run(
                    ["groupadd", "kids"], check=True, capture_output=True, text=True
                )
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to create group 'kids': {e.stderr}")
                return

        # Process each managed user
        for username in managed_users:
            if not self.user_exists(username):
                logger.warning(f"User '{username}' does not exist on system.")
                continue

            # Force refresh group membership to get current state
            # This is needed to handle cases where the group cache might be outdated
            try:
                subprocess.run(
                    ["getent", "group"], check=True, capture_output=True, text=True
                )
            except Exception as e:
                logger.warning(f"Failed to refresh group cache: {e}")

            try:
                # Get current groups for the user
                user_groups = [
                    g.gr_name for g in grp.getgrall() if username in g.gr_mem
                ]
                # Also get the primary group
                primary_group = grp.getgrgid(pwd.getpwnam(username).pw_gid).gr_name
                user_groups.append(primary_group)
                logger.debug(f"Current groups for {username}: {', '.join(user_groups)}")
            except Exception as e:
                logger.error(f"Could not determine groups for user {username}: {e}")
                continue

            # Add user to each required group if not already a member
            for group_name in required_groups:
                if group_name not in user_groups:
                    logger.info(f"Adding user '{username}' to group '{group_name}'.")
                    try:
                        # Run usermod with debug output
                        logger.info(
                            f"Adding user '{username}' to group '{group_name}' with usermod -aG"
                        )
                        result = subprocess.run(
                            ["usermod", "-aG", group_name, username],
                            check=True,
                            capture_output=True,
                            text=True,
                        )
                        logger.debug(f"usermod output: {result.stdout}")

                        # Verify the user was added to the group
                        verify_result = subprocess.run(
                            ["groups", username],
                            check=True,
                            capture_output=True,
                            text=True,
                        )
                        logger.info(
                            f"After adding to {group_name}, {username}'s groups: {verify_result.stdout.strip()}"
                        )

                        # Ensure group membership is immediately visible to the system
                        try:
                            # Update system group cache
                            subprocess.run(
                                ["getent", "group", group_name],
                                check=True,
                                capture_output=True,
                                text=True,
                            )
                        except Exception as e:
                            logger.warning(f"Failed to refresh group cache: {e}")
                    except subprocess.CalledProcessError as e:
                        logger.error(
                            f"Failed to add user '{username}' to group '{group_name}': {e.stderr}"
                        )
                else:
                    logger.debug(
                        f"User '{username}' is already in group '{group_name}'."
                    )

    def ensure_pam_time_module(self):
        """
        Ensures pam_time.so is active using two complementary approaches:

        1. Creates a custom authselect profile with pam_time.so in the system-auth
           stack (applies to all PAM services that include system-auth)

        2. Directly modifies /etc/pam.d/sddm to explicitly include pam_time.so
           before the system-account include (ensuring SDDM enforces time restrictions
           even if authselect updates the system files)

        This dual approach ensures maximum compatibility and resilience against
        system updates or configuration changes.
        """
        # First, directly modify SDDM's PAM config (safe from authselect overwrites)
        # This is the most reliable way to ensure SDDM enforces login time restrictions
        self._ensure_sddm_pam_time()

        # Also set up the authselect profile for system-wide consistency
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
        Creates /etc/dbus-1/system.d/guardian.conf to allow managed users access to org.guardian.Daemon.
        Both 'kids' and 'users' groups are given permissions to support transition periods.
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
                <!-- Explicitly allow sending to individual agent instances with PIDs -->
                <allow send_type="method_call" send_path="/org/guardian/Agent"/>
                <allow send_interface="org.guardian.Agent"/>
            </policy>

            <!-- Policy for kids group users -->
            <policy group="kids">
                <allow own_prefix="org.guardian.Agent"/>
                <allow send_destination="org.guardian.Daemon"/>
                <allow send_path="/org/guardian/Daemon"/>
                <allow send_interface="org.guardian.Daemon"/>
                <allow receive_user="root"/>
                <allow receive_sender="org.guardian.Daemon"/>
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

    def __init__(self, policy: Policy = None, tracker: "SessionTracker" = None):
        """
        Initialize the UserManager with a policy instance and optionally a session tracker.
        
        Args:
            policy: The Policy instance containing user rules and configurations
            tracker: The SessionTracker instance (optional, can be set later)
        
        Note:
            The tracker can be set later using set_tracker() to avoid circular dependencies
            during initialization.
        """
        self.policy = policy
        self.tracker = tracker

    def set_tracker(self, tracker: "SessionTracker"):
        """Set the session tracker after initialization to resolve circular dependencies."""
        self.tracker = tracker

    def setup_user_login(self, username: str) -> bool:
        """
        Comprehensive setup for a user upon login.
        Ensures group membership, PAM rules, and systemd service are correctly configured.
        """
        logger.info(f"Performing comprehensive login setup for user '{username}'")

        if not self.user_exists(username):
            logger.error(f"User '{username}' does not exist. Aborting setup.")
            return False

        try:
            # 1. Ensure group memberships are correct
            self.ensure_kids_group()

            # 2. Ensure PAM time rules are up-to-date
            self.write_time_rules()

            # 3. Ensure the user's agent service is set up and ready to run
            self.ensure_systemd_user_service(username)

            logger.info(f"Successfully completed login setup for user '{username}'")
            return True
        except Exception as e:
            logger.error(f"An error occurred during login setup for '{username}': {e}")
            return False

    def update_policy(self, policy: Policy):
        """
        Update the policy instance and re-evaluate rules.
        """
        self.policy = policy
        self.write_time_rules()
        self.ensure_kids_group()
        for username in self.policy.data.get("users", {}):
            if self.user_exists(username):
                self.setup_user_service(username)

    def write_time_rules(self):
        """
        Updates the time rules for all children according to the policy in /etc/security/time.conf,
        without overwriting foreign rules.

        This method:
        1. First checks if the file is excessively large and needs cleanup
        2. Compares existing content with what we need to write
        3. Only writes if content needs updating
        """
        try:
            # Ensure the PAM time module is loaded in the relevant PAM services
            # This now also ensures SDDM PAM configuration includes pam_time.so explicitly
            self.ensure_pam_time_module()

            # Check if the file exists and analyze its size/content
            if TIME_CONF_PATH.exists():
                file_size = TIME_CONF_PATH.stat().st_size
                if file_size == 0:
                    # Empty file, we'll just write fresh content
                    logger.info(f"{TIME_CONF_PATH} is empty, will write fresh content")
                elif (
                    file_size > 10000
                ):  # If file is over 10KB, it likely has duplicates
                    logger.warning(
                        f"time.conf is large ({file_size} bytes), likely contains duplicates. Will clean up."
                    )
                    self._cleanup_time_conf()

            # Generate the rules we need to enforce
            rules = self._generate_rules()
            managed_usernames = set(self.policy.data.get("users", {}).keys())

            # Generate the content we want to have in the file
            desired_content = ["# Managed by guardian-daemon"]
            preserved_lines = []
            existing_rules = set()

            # Extract any non-Guardian content to preserve
            if TIME_CONF_PATH.exists():
                try:
                    with open(TIME_CONF_PATH, "r") as f:
                        lines = f.readlines()

                    # Extract existing Guardian rules and preserved content
                    for line in lines:
                        line = line.strip()

                        # Skip empty lines and Guardian headers
                        if (
                            not line
                            or line.startswith("# Managed by guardian-daemon")
                            or line.startswith("# --- Guardian Managed Rules Below ---")
                        ):
                            continue

                        # Check if it's a rule that Guardian manages
                        try:
                            parts = line.split(";")
                            if len(parts) >= 3:
                                # Track existing Guardian rules
                                if (
                                    parts[2] in managed_usernames
                                    or parts[2] == "!@kids"
                                ):
                                    # This is a Guardian rule
                                    existing_rules.add(line)
                                    continue
                        except Exception:
                            pass

                        # This line is not managed by Guardian, preserve it
                        preserved_lines.append(line)
                except Exception as e:
                    logger.error(f"Error reading {TIME_CONF_PATH}: {e}")

            # Add the preserved content
            if preserved_lines:
                desired_content.append("")  # Empty line as separator
                desired_content.append("# Non-Guardian managed content (preserved)")
                desired_content.extend(preserved_lines)
                desired_content.append("")  # Empty line as separator

            # Add our rules
            desired_content.extend(rules)

            # Check if the current file content matches what we want
            current_content_matches = False
            if TIME_CONF_PATH.exists():
                try:
                    with open(TIME_CONF_PATH, "r") as f:
                        current_content = [
                            line.strip() for line in f.readlines() if line.strip()
                        ]

                    # Check if every rule we need is already in the file
                    desired_rules_set = set(rules)
                    if desired_rules_set.issubset(existing_rules):
                        # All our rules are already in the file

                        # Check if file doesn't have duplicates or extra Guardian rules
                        # by counting Guardian rules
                        guardian_rule_count = sum(
                            1
                            for line in current_content
                            if "!@kids;Al0000-2400" in line
                            or any(
                                f";{username};" in line
                                for username in managed_usernames
                            )
                        )

                        if guardian_rule_count == len(desired_rules_set):
                            logger.info(
                                "time.conf already contains all needed rules, no update needed"
                            )
                            current_content_matches = True
                except Exception as e:
                    logger.error(
                        f"Error checking current {TIME_CONF_PATH} content: {e}"
                    )

            # Only write if the content needs updating
            if not current_content_matches:
                logger.info(
                    f"Updating {TIME_CONF_PATH} with {len(rules)} managed rules"
                )
                try:
                    # Write the new content
                    with open(TIME_CONF_PATH, "w") as f:
                        for line in desired_content:
                            f.write(line + "\n")
                    os.chmod(TIME_CONF_PATH, 0o644)

                    # Reload PAM configuration if the system supports it
                    try:
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
                    logger.error(f"Error writing to {TIME_CONF_PATH}: {e}")
            else:
                logger.debug(f"No changes needed to {TIME_CONF_PATH}")

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

    def _cleanup_time_conf(self):
        """
        Clean up the time.conf file by removing duplicated rules and Guardian-managed content.
        This is a more aggressive cleanup than the normal filtering in write_time_rules.

        After cleanup, the file will have:
        1. Original comment header from the system
        2. Any preserved third-party rules
        3. No Guardian rules (these will be added fresh by write_time_rules)
        """
        if not TIME_CONF_PATH.exists():
            logger.debug("No time.conf file to clean up")
            return

        try:
            # Read the file
            with open(TIME_CONF_PATH, "r") as f:
                all_lines = f.readlines()

            # Count how many lines contained Guardian rules before cleanup
            guardian_rule_count = sum(
                1
                for line in all_lines
                if (
                    "!@kids;Al0000-2400" in line
                    or any(
                        f";{username};" in line
                        for username in self.policy.data.get("users", {})
                    )
                )
            )

            # Extract the system's comment header (should be preserved)
            header_lines = []
            in_header = True

            for line in all_lines:
                stripped = line.strip()
                if in_header:
                    # While in header section, keep original system comments
                    if (
                        stripped.startswith("#")
                        and not stripped.startswith("# Managed by guardian-daemon")
                        and not stripped.startswith(
                            "# --- Guardian Managed Rules Below ---"
                        )
                    ):
                        header_lines.append(line)
                    elif not stripped:
                        # Keep blank lines in header
                        header_lines.append(line)
                    else:
                        # Found first non-comment or Guardian-specific comment
                        # This means we've reached the end of the header
                        in_header = False

            # Find all non-Guardian rules
            kept_rules = []
            managed_usernames = set(self.policy.data.get("users", {}).keys())

            for line in all_lines:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    # Skip empty lines and comments
                    continue

                # Check if this is a Guardian rule
                try:
                    parts = stripped.split(";")
                    if len(parts) >= 3:
                        if parts[2] == "!@kids" or parts[2] in managed_usernames:
                            # This is a Guardian rule, skip it
                            continue
                        else:
                            # Non-Guardian rule, keep it
                            kept_rules.append(stripped)
                except Exception:
                    # If we can't parse it, keep it to be safe
                    kept_rules.append(stripped)

            # Remove duplicates while preserving order
            unique_kept_rules = []
            for rule in kept_rules:
                if rule not in unique_kept_rules:
                    unique_kept_rules.append(rule)

            # Write back a clean file with header + unique non-Guardian rules
            with open(TIME_CONF_PATH, "w") as f:
                # Write the original header comments
                for line in header_lines:
                    f.write(line)

                # Write unique non-Guardian rules if any
                if unique_kept_rules:
                    if header_lines and not header_lines[-1].strip() == "":
                        f.write("\n")  # Add blank line after header if needed
                    f.write("# Third-party rules preserved during Guardian cleanup\n")
                    for rule in unique_kept_rules:
                        f.write(f"{rule}\n")

            logger.info(
                f"Cleaned up time.conf: removed {guardian_rule_count} duplicate Guardian rules, kept {len(unique_kept_rules)} non-Guardian rules"
            )

        except Exception as e:
            logger.error(f"Error cleaning up time.conf: {e}")

    def _generate_rules(self):
        """
        Generates the PAM time rules from the policy.
        The default behavior of pam_time.so is to deny access if no rule matches.
        Therefore, we must create a rule to explicitly ALLOW non-managed users.
        """
        rules = []
        users = self.policy.data.get("users", {})
        managed_users = list(users.keys())

        # Start with an explanatory comment
        rules.append("# --- Guardian Managed Rules Below ---")

        # Generate rules for each managed user first
        # This follows the "first match wins" logic of pam_time.so
        # Each user gets their specific allow rules followed by a catch-all deny

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
                    # Use OR (|) to allow login during ANY of the specified day-time periods
                    combined_times = "|".join(time_specs)
                    rules.append(f"*;*;{username};{combined_times}")

        # Finally, add a rule to allow all users not in kids group at all times
        # This must come AFTER the specific user rules due to "first match wins"
        rules.append("*;*;!@kids;Al0000-2400")

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
        
        Security: Validates username and uses pwd.getpwnam() to prevent path traversal.
        """
        # Validate username format to prevent path traversal
        if not self.validate_username(username):
            logger.error(f"Invalid username format: {username}")
            return
        
        try:
            # Get canonical user info from system - prevents path traversal
            user_info = pwd.getpwnam(username)
            # Use the canonical home directory from system, not user input
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
                    # For non-logged-in users, we need to manually create the symlink to enable the service at next login

                    # Create the default.target.wants directory if it doesn't exist
                    default_target_dir = user_systemd_path / "default.target.wants"
                    symlink_path = default_target_dir / "guardian_agent.service"
                    logger.info(
                        f"Service file path: {service_file_path}, exists: {service_file_path.exists()}"
                    )
                    logger.info(
                        f"Default target dir: {default_target_dir}, exists: {default_target_dir.exists()}"
                    )
                    logger.info(
                        f"Symlink path: {symlink_path}, exists: {symlink_path.exists()}"
                    )

                    if not default_target_dir.exists():
                        logger.info(
                            f"Creating default.target.wants directory for {username}"
                        )
                        default_target_dir.mkdir(mode=0o755, exist_ok=True)
                        os.chown(default_target_dir, user_info.pw_uid, user_info.pw_gid)
                        logger.info(
                            f"Created directory {default_target_dir}, exists: {default_target_dir.exists()}"
                        )

                    # Create the symlink if it doesn't exist
                    if service_file_path.exists() and not symlink_path.exists():
                        logger.info(
                            f"Creating autostart symlink for guardian_agent.service for {username}"
                        )
                        try:
                            # Need to use relative path for the symlink target
                            os.symlink("../guardian_agent.service", symlink_path)
                            os.chown(
                                symlink_path,
                                user_info.pw_uid,
                                user_info.pw_gid,
                                follow_symlinks=False,
                            )
                            logger.info(
                                f"Successfully enabled guardian_agent service for {username}"
                            )
                        except FileExistsError:
                            logger.debug(
                                f"Symlink for guardian_agent.service already exists for {username}"
                            )
                        except Exception as e:
                            logger.error(
                                f"Failed to create symlink for {username}: {e}"
                            )
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
        
        Security: Validates username and uses pwd.getpwnam() to prevent path traversal.
        """
        # Validate username format to prevent path traversal
        if not self.validate_username(username):
            logger.error(f"Invalid username format: {username}")
            return
        
        try:
            # Get canonical user info from system - prevents path traversal
            user_info = pwd.getpwnam(username)
            # Use the canonical home directory from system, not user input
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
                if not result or result.stdout.strip() != "active":
                    logger.info(
                        f"Enabling and starting guardian_agent service for user {username}"
                    )
                    # Make sure the service is enabled first
                    self._run_systemctl_user_command(
                        username, "enable", "guardian_agent.service"
                    )
                    # Then start it
                    start_result = self._run_systemctl_user_command(
                        username, "start", "guardian_agent.service"
                    )
                    if not start_result:
                        logger.warning(
                            f"Failed to start guardian_agent service for user {username}"
                        )
                    else:
                        logger.info(
                            f"Successfully started guardian_agent service for user {username}"
                        )
                else:
                    logger.debug(
                        f"guardian_agent service for user {username} is already active"
                    )
            else:
                logger.info(
                    f"User {username} is not logged in, preparing service for next login"
                )
                # Ensure service file exists and is set to autostart
                service_dir = user_home / ".config/systemd/user"
                service_file = service_dir / "guardian_agent.service"

                # Create necessary directories if they don't exist
                if not service_dir.exists():
                    logger.info(f"Creating systemd user directory for {username}")
                    service_dir.mkdir(parents=True, exist_ok=True)
                    chown_recursive(service_dir, user_info.pw_uid, user_info.pw_gid)
                    logger.info(
                        f"Created service dir {service_dir}, exists: {service_dir.exists()}"
                    )

                # Ensure the service file exists
                if not service_file.exists() and SOURCE_SERVICE_FILE.exists():
                    logger.debug(f"Copying service file for {username}")
                    shutil.copy(SOURCE_SERVICE_FILE, service_file)
                    os.chown(service_file, user_info.pw_uid, user_info.pw_gid)
                    os.chmod(service_file, 0o644)  # rw-r--r--

                # Manually create the symlink for auto-start
                default_target_dir = service_dir / "default.target.wants"
                symlink_path = default_target_dir / "guardian_agent.service"

                # Create the default.target.wants directory if it doesn't exist
                if not default_target_dir.exists():
                    logger.debug(
                        f"Creating default.target.wants directory for {username}"
                    )
                    default_target_dir.mkdir(mode=0o755, exist_ok=True)
                    os.chown(default_target_dir, user_info.pw_uid, user_info.pw_gid)

                # Create the symlink if it doesn't exist and service file exists
                if service_file.exists() and not symlink_path.exists():
                    logger.info(
                        f"Creating autostart symlink for guardian_agent.service for {username}"
                    )
                    try:
                        # Need to use relative path for the symlink target
                        os.symlink("../guardian_agent.service", symlink_path)
                        os.chown(
                            symlink_path,
                            user_info.pw_uid,
                            user_info.pw_gid,
                            follow_symlinks=False,
                        )
                        logger.info(
                            f"Successfully enabled guardian_agent service for {username}"
                        )
                    except FileExistsError:
                        logger.debug(
                            f"Symlink for guardian_agent.service already exists for {username}"
                        )
                    except Exception as e:
                        logger.error(f"Failed to create symlink for {username}: {e}")

        except KeyError:
            logger.error(f"User '{username}' not found, cannot ensure systemd service.")
        except Exception as e:
            logger.error(f"Failed to ensure systemd user service for {username}: {e}")

        # User login setup continues in the main setup_user_login method

        if not self.user_exists(username):
            logger.warning(f"User '{username}' does not exist, cannot set up login.")
            return False

        # Step 1: Update PAM time rules for all users
        self.write_time_rules()

        # Step 2: Ensure user is in required groups
        logger.info(f"Ensuring {username} is in required groups")
        self.ensure_kids_group()

        # Step 3: Set up and activate systemd user service
        logger.info(f"Ensuring guardian agent service is running for {username}")
        self.ensure_systemd_user_service(username)

        logger.info(f"User login setup complete for {username}")
        return True

    def _ensure_sddm_pam_time(self):
        """
        Ensures pam_time.so is explicitly added to SDDM's PAM account phase.

        Based on the way authselect works in Fedora/Nobara, we can safely modify
        /etc/pam.d/sddm directly, as it's not managed by authselect.
        The correct approach is to add pam_time.so after pam_nologin.so but before
        the 'include password-auth' line to ensure it's checked before other account validations.
        """
        sddm_pam_path = Path("/etc/pam.d/sddm")
        if not sddm_pam_path.exists():
            logger.warning("SDDM PAM configuration not found, skipping SDDM PAM fix.")
            return False

        # Create a backup if it doesn't already exist
        backup_path = Path(f"{sddm_pam_path}.guardian.bak")
        if not backup_path.exists():
            try:
                shutil.copy2(sddm_pam_path, backup_path)
                logger.info(f"Created backup of SDDM PAM config at {backup_path}")
            except Exception as e:
                logger.error(f"Failed to create SDDM PAM config backup: {e}")
                return False

        try:
            # Read the current configuration
            with open(sddm_pam_path, "r") as f:
                content = f.read()
                logger.debug(f"Original SDDM PAM configuration:\n{content}")

            # Reset file pointer and read lines
            with open(sddm_pam_path, "r") as f:
                lines = f.readlines()

            # Check if pam_time.so is already explicitly included
            if any("account" in line and "pam_time.so" in line for line in lines):
                logger.info(
                    "SDDM PAM config already includes pam_time.so - curfew enforcement active"
                )
                return True

            # Find the position to insert pam_time.so (between pam_nologin and password-auth)
            modified_lines = []
            added_pam_time = False

            for i, line in enumerate(lines):
                modified_lines.append(line)

                # Find the account section with pam_nologin.so and add pam_time.so after it
                if (
                    not added_pam_time
                    and line.strip().startswith("account")
                    and "required" in line
                    and "pam_nologin.so" in line
                    and i + 1 < len(lines)
                    and "account" in lines[i + 1]
                    and "include" in lines[i + 1]
                ):
                    # Add pam_time.so after pam_nologin but before include password-auth
                    # Match the spacing of the existing file
                    modified_lines.append("account     required      pam_time.so\n")
                    added_pam_time = True
                    logger.info(
                        "Adding pam_time.so after pam_nologin.so in SDDM PAM configuration - enabling curfew enforcement"
                    )

            if not added_pam_time:
                # If we didn't find the exact pattern, try a more general approach
                for i, line in enumerate(lines):
                    if (
                        not added_pam_time
                        and i + 1 < len(lines)
                        and line.strip().startswith("account")
                        and "include" in lines[i + 1]
                    ):
                        # Insert just before the include line
                        modified_lines.insert(
                            i + 1, "account     required      pam_time.so\n"
                        )
                        added_pam_time = True
                        logger.info(
                            "Adding pam_time.so before account include in SDDM PAM config - enabling curfew enforcement"
                        )
                        break

            if not added_pam_time:
                logger.warning(
                    "Could not locate proper position to add pam_time.so in SDDM config"
                )
                return False

            # Write the modified configuration
            with open(sddm_pam_path, "w") as f:
                f.writelines(modified_lines)

            # Read back and log the modified content
            with open(sddm_pam_path, "r") as f:
                modified_content = f.read()
                logger.debug(f"Modified SDDM PAM configuration:\n{modified_content}")

            logger.info(
                "Successfully added pam_time.so to SDDM PAM configuration - graphical login curfew enforcement is now active"
            )

            # Verify the change with a quick test
            try:
                result = subprocess.run(
                    ["grep", "pam_time.so", str(sddm_pam_path)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    logger.debug(
                        f"Verified pam_time.so was added: {result.stdout.strip()}"
                    )
                else:
                    logger.warning(
                        "Failed to find pam_time.so in SDDM configuration after modification"
                    )
            except Exception as e:
                logger.warning(f"Could not verify SDDM PAM configuration: {e}")

            return True

        except Exception as e:
            logger.error(f"Failed to modify SDDM PAM configuration: {e}")

            # Try to restore from backup if modification failed
            try:
                if backup_path.exists():
                    shutil.copy2(backup_path, sddm_pam_path)
                    logger.info("Restored SDDM PAM configuration from backup")
            except Exception as restore_error:
                logger.error(
                    f"Failed to restore SDDM PAM config from backup: {restore_error}"
                )

            return False

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
