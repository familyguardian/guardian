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
        # Core PAM services that need to be configured
        PAM_SERVICES = [
            "sddm",
            "login",
            "kde",
            "lightdm",
            "gdm",
            "xdm",
        ]

        # Services potentially managed by authselect
        AUTHSELECT_SERVICES = ["system-auth", "password-auth"]

        # Additional services to check for (but not create if they don't exist)
        OPTIONAL_SERVICES = ["common-account", "common-auth", "common-session"]

        # Check if we have authselect available on this system
        authselect_available = False
        try:
            result = subprocess.run(
                ["which", "authselect"], check=False, capture_output=True, text=True
            )
            authselect_available = result.returncode == 0
            if authselect_available:
                logger.info("authselect detected - will use it for PAM configuration")
        except Exception:
            logger.debug("authselect not available")

        # First handle authselect-managed services if available
        if authselect_available:
            try:
                # Determine current profile
                current_profile = None
                try:
                    result = subprocess.run(
                        ["authselect", "current"],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    current_profile = result.stdout.strip().split("\n")[0]
                    logger.info(f"Current authselect profile: {current_profile}")
                except Exception as e:
                    logger.warning(
                        f"Could not determine current authselect profile: {e}"
                    )

                # Check if we need to modify authselect configuration
                auth_files_need_update = False
                for service in AUTHSELECT_SERVICES:
                    service_file = Path(f"/etc/pam.d/{service}")
                    if not service_file.exists():
                        continue

                    with open(service_file, "r") as f:
                        content = f.read()
                    if "pam_time.so" not in content:
                        auth_files_need_update = True
                        break

                if auth_files_need_update:
                    # Method 1: Direct file modification (most reliable but gets overwritten)
                    # This is a temporary fix that will be overwritten on next authselect run
                    for service in AUTHSELECT_SERVICES:
                        service_file = Path(f"/etc/pam.d/{service}")
                        if not service_file.exists():
                            continue

                        with open(service_file, "r") as f:
                            lines = f.readlines()

                        # Find the account section and add our rule after it
                        account_lines = []
                        for i, line in enumerate(lines):
                            if line.strip().startswith(
                                "account"
                            ) and not line.strip().startswith("#"):
                                account_lines.append(i)

                        if account_lines:
                            insert_pos = max(account_lines) + 1
                            # Make backup
                            shutil.copy(
                                service_file,
                                service_file.with_suffix(f".bak-{int(time.time())}"),
                            )

                            # Add our line
                            lines.insert(
                                insert_pos, "account    required    pam_time.so\n"
                            )
                            with open(service_file, "w") as f:
                                f.writelines(lines)
                            logger.info(
                                f"Added pam_time.so to {service_file} (temporary)"
                            )

                    # Method 2: Handle Local profile by creating a custom profile based on it
                    try:
                        # Check if we're using the local profile
                        is_local_profile = False
                        if current_profile and "local" in current_profile.lower():
                            is_local_profile = True
                            logger.info(
                                "Detected Local authselect profile - will create custom copy"
                            )

                        if is_local_profile:
                            # Create a guardian-local profile based on the Local profile
                            guardian_profile_name = "guardian-local"
                            custom_profile_path = Path(
                                f"/etc/authselect/custom/{guardian_profile_name}"
                            )

                            # Check if our custom profile already exists
                            if custom_profile_path.exists():
                                logger.info(
                                    f"Custom profile {guardian_profile_name} already exists"
                                )
                            else:
                                # Create the custom profile
                                try:
                                    subprocess.run(
                                        [
                                            "authselect",
                                            "create-profile",
                                            guardian_profile_name,
                                            "--base-on=local",
                                        ],
                                        check=True,
                                        capture_output=True,
                                        text=True,
                                    )
                                    logger.info(
                                        f"Created custom profile {guardian_profile_name} based on local profile"
                                    )
                                except subprocess.CalledProcessError as e:
                                    logger.error(
                                        f"Failed to create custom profile: {e.stderr}"
                                    )
                                    raise

                            # Modify the profile files to add pam_time.so
                            for service in AUTHSELECT_SERVICES:
                                profile_file = custom_profile_path / service

                                if profile_file.exists():
                                    # Read the current content
                                    with open(profile_file, "r") as f:
                                        content = f.read()

                                    # Check if pam_time.so already exists
                                    if "pam_time.so" in content:
                                        logger.info(
                                            f"Custom profile {service} already contains pam_time.so"
                                        )
                                        continue

                                    # Add the pam_time.so module after account modules
                                    lines = content.split("\n")
                                    account_lines = [
                                        i
                                        for i, line in enumerate(lines)
                                        if line.strip().startswith("account")
                                        and not line.strip().startswith("#")
                                    ]

                                    if account_lines:
                                        # Insert after the last account line
                                        insert_pos = max(account_lines) + 1
                                        lines.insert(
                                            insert_pos,
                                            "account    required    pam_time.so",
                                        )

                                        # Write the updated content
                                        with open(profile_file, "w") as f:
                                            f.write("\n".join(lines))

                                        logger.info(
                                            f"Added pam_time.so to custom profile {service}"
                                        )
                                    else:
                                        # No account section found, append it
                                        with open(profile_file, "a") as f:
                                            f.write(
                                                "\n# Added by Guardian for time restrictions\n"
                                            )
                                            f.write(
                                                "account    required    pam_time.so\n"
                                            )
                                        logger.info(
                                            f"Appended pam_time.so to custom profile {service}"
                                        )

                                        # Select the custom profile
                            try:
                                # Get current features if any
                                current_features = []
                                try:
                                    # Try getting the features with the more reliable "current" command
                                    result = subprocess.run(
                                        ["authselect", "current"],
                                        check=True,
                                        capture_output=True,
                                        text=True,
                                    )

                                    # Parse the output which is in a more human-readable format
                                    lines = result.stdout.strip().split("\n")
                                    feature_section = False

                                    for line in lines:
                                        # Skip the first line which is the profile ID
                                        if (
                                            "Profil ID:" in line
                                            or "Profile ID:" in line
                                        ):
                                            continue

                                        # Detect the feature section header
                                        if (
                                            "Aktivierte Funktionen:" in line
                                            or "Enabled features:" in line
                                        ):
                                            feature_section = True
                                            continue

                                        # Once in the feature section, collect features
                                        if (
                                            feature_section
                                            and line.strip()
                                            and "- " in line
                                        ):
                                            feature = line.strip().replace("- ", "")
                                            if feature:
                                                current_features.append(feature)

                                    logger.info(
                                        f"Detected features to preserve: {current_features}"
                                    )
                                except Exception as e:
                                    # Fallback to the raw method
                                    logger.warning(
                                        f"Failed to get features from current command: {e}"
                                    )
                                    try:
                                        profile_info = subprocess.run(
                                            ["authselect", "current", "--raw"],
                                            check=True,
                                            capture_output=True,
                                            text=True,
                                        ).stdout.strip()

                                        if "with-" in profile_info:
                                            for line in profile_info.split("\n"):
                                                if line.strip().startswith("with-"):
                                                    current_features.append(
                                                        line.strip()
                                                    )
                                    except Exception:
                                        logger.warning(
                                            "Could not determine current authselect features"
                                        )

                                # Select our custom profile with the same features as the current profile
                                cmd = [
                                    "authselect",
                                    "select",
                                    f"custom/{guardian_profile_name}",
                                ]
                                if current_features:
                                    # Log the features we're preserving
                                    logger.info(
                                        f"Preserving features: {', '.join(current_features)}"
                                    )
                                    cmd.extend(current_features)
                                cmd.append("--force")

                                subprocess.run(
                                    cmd, check=True, capture_output=True, text=True
                                )
                                logger.info(
                                    f"Selected custom profile {guardian_profile_name} with pam_time.so and preserved features"
                                )
                            except subprocess.CalledProcessError as e:
                                logger.error(
                                    f"Failed to select custom profile: {e.stderr}"
                                )
                                raise

                        else:
                            # Create a custom profile based on the current one
                            current_base = "sssd"  # Default base
                            if current_profile:
                                # Try to determine the actual base
                                if "/" in current_profile:
                                    parts = current_profile.split("/")
                                    if len(parts) > 1 and parts[0] != "custom":
                                        current_base = parts[0]
                                else:
                                    current_base = current_profile.split()[0]

                            # Get current features
                            current_features = []
                            try:
                                profile_info = subprocess.run(
                                    ["authselect", "current", "--raw"],
                                    check=True,
                                    capture_output=True,
                                    text=True,
                                ).stdout.strip()

                                if "with-" in profile_info:
                                    # Extract feature names
                                    for line in profile_info.split("\n"):
                                        if line.strip().startswith("with-"):
                                            current_features.append(line.strip())
                            except Exception:
                                logger.warning(
                                    "Could not determine current authselect features"
                                )

                            # Create guardian-time custom feature
                            custom_feature_dir = Path(
                                "/etc/authselect/custom-features/guardian-time"
                            )
                            custom_feature_dir.mkdir(parents=True, exist_ok=True)

                            # Create feature files for system-auth and password-auth
                            for service in AUTHSELECT_SERVICES:
                                with open(custom_feature_dir / service, "w") as f:
                                    f.write("account    required    pam_time.so\n")

                            # Create README
                            with open(custom_feature_dir / "README", "w") as f:
                                f.write(
                                    "Guardian time restriction feature for parental controls\n"
                                )

                            # Create a guardian profile if we're not already using custom
                            if not current_profile or not current_profile.startswith(
                                "custom/"
                            ):
                                subprocess.run(
                                    [
                                        "authselect",
                                        "create-profile",
                                        "guardian",
                                        f"--base-on={current_base}",
                                    ],
                                    check=True,
                                    capture_output=True,
                                    text=True,
                                )
                                logger.info(
                                    f"Created custom guardian profile based on {current_base}"
                                )

                                # Select the custom profile with our feature
                                cmd = [
                                    "authselect",
                                    "select",
                                    "custom/guardian",
                                    "with-guardian-time",
                                ]
                                cmd.extend(current_features)
                                cmd.append("--force")

                                subprocess.run(
                                    cmd, check=True, capture_output=True, text=True
                                )
                                logger.info(
                                    "Applied custom guardian profile with pam_time.so feature"
                                )
                            else:
                                # Just enable our feature on the existing custom profile
                                cmd = [
                                    "authselect",
                                    "select",
                                    current_profile,
                                    "with-guardian-time",
                                ]
                                if current_features:
                                    cmd.extend(current_features)
                                cmd.append("--force")

                                subprocess.run(
                                    cmd, check=True, capture_output=True, text=True
                                )
                                logger.info(
                                    f"Added guardian-time feature to existing {current_profile}"
                                )
                    except Exception as e:
                        logger.warning(f"Failed to configure authselect: {e}")

                        # Fallback to direct file modification as a last resort
                        try:
                            for service in AUTHSELECT_SERVICES:
                                service_file = Path(f"/etc/pam.d/{service}")
                                if service_file.exists():
                                    # Take extra precautions - use a persistent file
                                    guardian_pam_file = Path(
                                        f"/etc/pam.d/guardian-{service}-time"
                                    )
                                    with open(guardian_pam_file, "w") as f:
                                        f.write(
                                            "# Guardian time restrictions - DO NOT REMOVE\n"
                                        )
                                        f.write("account    required    pam_time.so\n")
                                    logger.info(
                                        f"Created persistent rule file {guardian_pam_file}"
                                    )

                                    # Try to add include to the main file
                                    with open(service_file, "r") as f:
                                        lines = f.readlines()

                                    # Find a suitable position after any account line
                                    account_lines = [
                                        i
                                        for i, line in enumerate(lines)
                                        if line.strip().startswith("account")
                                        and not line.strip().startswith("#")
                                    ]

                                    if account_lines:
                                        insert_pos = max(account_lines) + 1
                                        include_line = (
                                            f"@include guardian-{service}-time\n"
                                        )

                                        # Only add if not already there
                                        if include_line not in "".join(lines):
                                            lines.insert(insert_pos, include_line)

                                            with open(service_file, "w") as f:
                                                f.writelines(lines)
                                            logger.info(
                                                f"Added include to {service_file}"
                                            )

                            logger.info(
                                "Applied persistent PAM time module through include files"
                            )
                        except Exception as fallback_error:
                            logger.error(
                                f"All authselect methods failed: {fallback_error}"
                            )

                    logger.info(
                        "Applied authselect custom profile with pam_time.so configuration"
                    )
            except Exception as e:
                logger.error(f"Failed to configure authselect: {e}")

        # Then handle core PAM services - create files if needed
        for service in PAM_SERVICES:
            service_file = Path(f"/etc/pam.d/{service}")
            if not service_file.exists():
                continue

            try:
                with open(service_file, "r") as f:
                    content = f.read()

                # Check if the file is managed by authselect
                is_managed_by_authselect = "Generated by authselect" in content

                if is_managed_by_authselect:
                    logger.info(
                        f"Skipping {service_file} as it's managed by authselect"
                    )
                    continue

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

        # Now handle optional services - only modify if they already exist
        for service in OPTIONAL_SERVICES:
            service_file = Path(f"/etc/pam.d/{service}")
            if not service_file.exists():
                logger.debug(
                    f"Optional PAM service file {service} does not exist, skipping"
                )
                continue

            try:
                with open(service_file, "r") as f:
                    content = f.read()

                if "pam_time.so" not in content:
                    # Make a backup of the original file
                    backup_file = service_file.with_suffix(f".bak-{int(time.time())}")
                    shutil.copy(service_file, backup_file)

                    # For optional files, just append to end if we can't find account section
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
                    else:
                        # Just append to the end
                        lines.append("# Added by guardian-daemon")
                        lines.append("account    required    pam_time.so")

                    # Write the updated file
                    with open(service_file, "w") as f:
                        f.write("\n".join(lines))

                    logger.info(
                        f"Added pam_time.so module to optional service {service_file}"
                    )
            except Exception as e:
                logger.error(
                    f"Failed to update optional PAM configuration for {service}: {e}"
                )

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
        Generates the PAM time rules from the policy
        """
        rules = []
        users = self.policy.data.get("users", {})

        # List of services to protect with time rules
        services = ["login", "sddm", "gdm", "lightdm", "xdm", "kde"]

        for username, user_policy in users.items():
            curfew = user_policy.get("curfew", self.policy.get_default("curfew"))
            # Beispiel: weekdays: "08:00-20:00"
            if curfew:
                # 1. First, add EXPLICIT DENY rules using negation - these are stronger

                # Map day strings to standard formats
                day_mapping = {
                    "weekdays": "Mo|Tu|We|Th|Fr",
                    "saturday": "Sa",
                    "sunday": "Su",
                    "all": "Al",
                }

                # For each service, create comprehensive rules
                for service in services:
                    for day, times in curfew.items():
                        # First, allow during allowed hours
                        standard_day = day_mapping.get(day, day)
                        rules.append(f"{service};*;{username};{standard_day};{times}")

                        # Then explicitly deny outside those hours on the same days
                        # This negation pattern is important: !times means EXCEPT during times
                        rules.append(f"{service};*;{username};{standard_day};!{times}")

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
