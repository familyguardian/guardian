"""
IPC server for admin commands of the Guardian Daemon.
"""

import asyncio
import grp
import inspect
import json
import os

from guardian_daemon.logging import get_logger
from guardian_daemon.policy import Policy
from guardian_daemon.sessions import SessionTracker
from guardian_daemon.systemd_manager import SYSTEMD_PATH, SystemdManager

logger = get_logger("IPCServer")


class GuardianIPCServer:
    """
    IPC server for admin commands of the Guardian Daemon.
    Provides a socket interface for status and control commands.
    """

    def __init__(self, config, tracker: SessionTracker, policy: Policy):
        """
        Initializes the IPC server and opens the Unix socket.

        Args:
            config (dict): Configuration data
            tracker (SessionTracker): The main session tracker instance.
            policy (Policy): The main policy instance.
        """
        self.config = config
        self.tracker = tracker
        self.policy = policy
        # Access the user manager from the session tracker
        self.user_manager = (
            self.tracker.user_manager if hasattr(self.tracker, "user_manager") else None
        )
        self.socket_path = self.config.get("ipc_socket", "/run/guardian-daemon.sock")
        self.admin_group = self.config.get("ipc_admin_group")
        if self.admin_group:
            try:
                self.admin_gid = grp.getgrnam(self.admin_group).gr_gid
            except KeyError:
                logger.error(
                    f"Admin group '{self.admin_group}' not found. IPC will only be available to root."
                )
                self.admin_gid = None
        else:
            self.admin_gid = None

        self.server = None
        self.handlers = {
            "list_kids": self.handle_list_kids,
            "get_quota": self.handle_get_quota,
            "get_curfew": self.handle_get_curfew,
            "list_timers": self.handle_list_timers,
            "reload_timers": self.handle_reload_timers,
            "reset_quota": self.handle_reset_quota,
            "describe_commands": self.handle_describe_commands,
            "setup-user": self.handle_setup_user,
            "sync_users_from_config": self.handle_sync_users_from_config,
            "add_user": self.handle_add_user,
            "update_user": self.handle_update_user,
        }

    async def start(self):
        """
        Starts the IPC server.
        """
        if os.path.exists(self.socket_path):
            logger.debug(f"Removing existing socket file: {self.socket_path}")
            os.remove(self.socket_path)

        self.server = await asyncio.start_unix_server(
            self.handle_connection, path=self.socket_path
        )

        # Set permissions on the socket
        if self.admin_gid is not None:
            os.chown(self.socket_path, -1, self.admin_gid)
            os.chmod(self.socket_path, 0o660)
        else:
            os.chmod(self.socket_path, 0o600)

        logger.info(f"IPC server started on {self.socket_path}")

    async def handle_connection(self, reader, writer):
        """
        Handles an incoming client connection.
        """
        peer_creds = writer.get_extra_info("peereid")

        # Handle case where peer credentials might be None
        if peer_creds is None:
            logger.warning("Could not get peer credentials. Assuming root user.")
            peer_uid = 0
            peer_gid = 0
        else:
            try:
                peer_uid, peer_gid, _ = peer_creds
            except (ValueError, TypeError) as e:
                logger.warning(
                    f"Invalid peer credentials format: {peer_creds}, error: {e}. Assuming root user."
                )
                peer_uid = 0
                peer_gid = 0

        if peer_uid != 0 and (self.admin_gid is None or peer_gid != self.admin_gid):
            logger.warning(
                f"Unauthorized IPC connection from UID={peer_uid}, GID={peer_gid}. Closing."
            )
            writer.close()
            await writer.wait_closed()
            return

        try:
            # Read message length (4 bytes)
            len_data = await reader.readexactly(4)
            msg_len = int.from_bytes(len_data, "big")

            # Read message
            data = await reader.readexactly(msg_len)
            message = data.decode().strip()
            logger.debug(f"Received IPC command: {message}")

            cmd, *args = message.split(" ", 1)
            handler = self.handlers.get(cmd)

            if handler:
                arg = args[0] if args else None
                logger.debug(f"Dispatching handler for command: {cmd} with arg: {arg}")
                try:
                    # Await handler if it's a coroutine
                    if asyncio.iscoroutinefunction(handler):
                        response = await handler(arg)
                    else:
                        response = handler(arg)

                    response_data = response.encode()
                    # Send response length then response
                    writer.write(len(response_data).to_bytes(4, "big"))
                    writer.write(response_data)
                    await writer.drain()
                    logger.debug(f"Sent response: {response}")
                except Exception as e:
                    logger.error(f"Error handling command '{cmd}': {e}")
                    error_response = json.dumps({"error": str(e)})
                    error_data = error_response.encode()
                    writer.write(len(error_data).to_bytes(4, "big"))
                    writer.write(error_data)
                    await writer.drain()
            else:
                logger.warning(f"Unknown IPC command: {cmd}")
                unknown_cmd_response = json.dumps({"error": "Unknown command"})
                unknown_cmd_data = unknown_cmd_response.encode()
                writer.write(len(unknown_cmd_data).to_bytes(4, "big"))
                writer.write(unknown_cmd_data)
                await writer.drain()

        except asyncio.IncompleteReadError:
            logger.warning("Client closed connection before sending full message.")
        except Exception as e:
            logger.error(f"Error in IPC connection handler: {e}")
        finally:
            writer.close()
            await writer.wait_closed()

    def handle_list_kids(self, _):
        """
        Returns the list of all kids (users).
        """
        kids = self.policy.get_all_usernames()
        logger.debug(f"Listing kids: {kids}")
        return json.dumps({"kids": kids})

    async def handle_get_quota(self, kid):
        """
        Returns the current quota status of a kid.
        All time values are in minutes in the returned JSON.

        Args:
            kid (str): Username

        Returns:
            str: JSON string with quota information (used, limit, remaining in minutes)
        """
        if not kid:
            logger.warning("get_quota called without kid argument")
            return json.dumps({"error": "missing kid"})

        user_policy = self.policy.get_user_policy(kid)
        if user_policy is None:
            logger.warning(f"get_quota: unknown kid '{kid}'")
            return json.dumps({"error": "unknown kid"})

        total_time = await self.tracker.get_total_time(kid)
        remaining_time = await self.tracker.get_remaining_time(kid)
        used_time = total_time - remaining_time

        logger.debug(
            f"Quota for {kid}: used={used_time}, limit={total_time}, remaining={remaining_time}"
        )
        return json.dumps(
            {
                "kid": kid,
                "used": round(used_time, 1),  # in minutes (API contract)
                "limit": round(total_time, 1),  # in minutes (API contract)
                "remaining": round(remaining_time, 1),  # in minutes (API contract)
            }
        )

    def handle_get_curfew(self, kid):
        """
        Returns the current curfew times of a kid.

        Args:
            kid (str): Username
        """
        if not kid:
            logger.warning("get_curfew called without kid argument")
            return json.dumps({"error": "missing kid"})
        user_policy = self.policy.get_user_policy(kid)
        if user_policy is None:
            logger.warning(f"get_curfew: unknown kid '{kid}'")
            return json.dumps({"error": "unknown kid"})
        curfew = user_policy.get("curfew")
        if curfew is None:
            curfew = self.policy.get_default("curfew")
        logger.debug(f"Curfew for {kid}: {curfew}")
        return json.dumps({"kid": kid, "curfew": curfew})

    def handle_list_timers(self, _):
        """
        Lists all active Guardian timers.
        """

        timers = []
        for f in os.listdir(SYSTEMD_PATH):
            if f.endswith(".timer") and f.startswith("guardian-"):
                timers.append(f[:-6])
        logger.debug(f"Active timers: {timers}")
        return json.dumps({"timers": timers})

    def handle_reload_timers(self, _):
        """
        Reloads the timer configuration.
        """
        mgr = SystemdManager()
        mgr.create_daily_reset_timer()
        logger.info("Timers reloaded via IPC")
        return json.dumps({"status": "timers reloaded"})

    async def handle_reset_quota(self, _):
        """
        Resets the daily quota for all users (deletes sessions since last reset).
        """
        await self.tracker.perform_daily_reset()
        logger.info("Quota reset for all users via IPC")
        return json.dumps({"status": "quota reset"})

    def handle_setup_user(self, username):
        """
        Sets up a user with Guardian (adds to groups, creates systemd services, etc).

        Args:
            username (str): Username of the user to set up
        """
        if not username:
            logger.warning("setup-user called without username argument")
            return json.dumps({"error": "missing username"})

        if not self.user_manager:
            logger.error("User manager not available, cannot set up user")
            return json.dumps({"error": "user manager not available"})

        try:
            # First check if user exists
            if not self.user_manager.user_exists(username):
                logger.warning(f"setup-user: user '{username}' does not exist")
                return json.dumps({"error": f"user '{username}' does not exist"})

            # Add user to policy if not already present
            if username not in self.policy.data.get("users", {}):
                logger.info(f"Adding user '{username}' to policy")
                self.policy.add_user(username)

            # Set up user login (groups, services, etc)
            result = self.user_manager.setup_user_login(username)
            if result:
                logger.info(f"User '{username}' successfully set up")
                return json.dumps(
                    {
                        "status": "success",
                        "message": f"User '{username}' set up successfully",
                    }
                )
            else:
                logger.error(f"Failed to set up user '{username}'")
                return json.dumps({"error": f"failed to set up user '{username}'"})
        except Exception as e:
            logger.error(f"Error setting up user '{username}': {e}")
            return json.dumps({"error": str(e)})

    def handle_describe_commands(self, _):
        """
        Returns a description of all available IPC commands and their parameters as JSON.
        This is used by the CLI for automatic command discovery.
        """
        commands = {}
        for cmd, handler in self.handlers.items():
            # Extract and clean up docstring
            desc = ""
            if handler.__doc__:
                desc = handler.__doc__.strip().split("\n")[0]  # First line only

            # Get function signature parameters
            sig = inspect.signature(handler)
            params = [
                p.name
                for p in sig.parameters.values()
                if p.name != "self" and p.name != "_"
            ]

            # Create command info
            commands[cmd] = {
                "description": desc,
                "params": params,
                "is_async": asyncio.iscoroutinefunction(handler),
            }

        logger.debug(f"Describing IPC commands: {list(commands.keys())}")
        return json.dumps(commands)

    def handle_sync_users_from_config(self, _):
        """
        Reset user settings in the database to match the configuration file.
        This also imports new users from the config to the database.
        """
        try:
            # Get all users from the config
            config_users = self.policy.data.get("users", {})
            defaults = self.policy.data.get("defaults", {})

            # Get all users currently in the database
            db_users = []
            current_settings = {}

            # Store settings for all users in the database
            for username in self.policy.get_all_usernames():
                db_users.append(username)
                user_settings = self.policy.get_user_policy(username)
                if user_settings:
                    current_settings[username] = user_settings

            # Track changes for reporting
            updated = []
            added = []

            # Update existing users in the database with config settings
            for username, settings in config_users.items():
                if not settings:
                    settings = defaults

                if username in db_users:
                    # User exists in DB, update settings
                    self.policy.storage.set_user_settings(username, settings)
                    updated.append(username)
                    logger.info(f"Updated settings for existing user: {username}")
                else:
                    # User not in DB, add them
                    self.policy.add_user(username)
                    self.policy.storage.set_user_settings(username, settings)
                    added.append(username)
                    logger.info(f"Added new user from config: {username}")

            return json.dumps(
                {
                    "status": "success",
                    "updated": updated,
                    "added": added,
                    "message": f"Synchronized {len(updated)} existing users and added {len(added)} new users",
                }
            )
        except Exception as e:
            logger.error(f"Error synchronizing users from config: {e}")
            return json.dumps({"error": str(e)})

    def handle_add_user(self, username):
        """
        Add a new user to the database with default settings.

        Args:
            username (str): Username to add
        """
        if not username:
            logger.warning("add_user called without username argument")
            return json.dumps({"error": "missing username"})

        try:
            # Check if user already exists
            if username in self.policy.get_all_usernames():
                logger.warning(f"User '{username}' already exists in the database")
                return json.dumps(
                    {"error": f"User '{username}' already exists", "status": "exists"}
                )

            # Add user with default settings
            if self.policy.add_user(username):
                logger.info(f"Added new user with default settings: {username}")
                return json.dumps(
                    {
                        "status": "success",
                        "message": f"Added user '{username}' with default settings",
                    }
                )
            else:
                logger.error(f"Failed to add user: {username}")
                return json.dumps(
                    {"error": f"Failed to add user '{username}'", "status": "error"}
                )
        except Exception as e:
            logger.error(f"Error adding user '{username}': {e}")
            return json.dumps({"error": str(e)})

    def handle_update_user(self, args):
        """
        Update a specific setting for a user.

        Args:
            args (str): Format should be "username setting_key setting_value"
        """
        if not args or len(args.split()) < 3:
            logger.warning("update_user called with invalid arguments")
            return json.dumps(
                {
                    "error": "Invalid arguments. Format should be: username setting_key setting_value",
                    "status": "error",
                }
            )

        try:
            # Parse arguments
            parts = args.split(maxsplit=2)
            username, setting_key, setting_value = parts

            # Valid settings that can be updated
            valid_settings = [
                "daily_quota_minutes",
                "curfew",
                "grace_minutes",
                "bonus_pool_minutes",
            ]

            if setting_key not in valid_settings:
                logger.warning(f"Invalid setting key: {setting_key}")
                return json.dumps(
                    {
                        "error": f"Invalid setting key. Must be one of: {', '.join(valid_settings)}",
                        "status": "error",
                    }
                )

            # Get current user settings
            user_settings = self.policy.get_user_policy(username)
            if not user_settings:
                logger.warning(f"User '{username}' not found in database")
                return json.dumps(
                    {"error": f"User '{username}' not found", "status": "not_found"}
                )

            # Parse and validate setting value based on setting type
            if (
                setting_key == "daily_quota_minutes"
                or setting_key == "grace_minutes"
                or setting_key == "bonus_pool_minutes"
            ):
                try:
                    # Convert to int for numeric settings
                    setting_value = int(setting_value)
                    if setting_value < 0:
                        return json.dumps(
                            {
                                "error": f"{setting_key} cannot be negative",
                                "status": "error",
                            }
                        )
                except ValueError:
                    return json.dumps(
                        {
                            "error": f"Invalid value for {setting_key}. Must be a number.",
                            "status": "error",
                        }
                    )
            elif setting_key == "curfew":
                try:
                    # Parse as JSON for complex settings
                    setting_value = json.loads(setting_value)
                    # Validate curfew format
                    required_keys = ["weekdays", "saturday", "sunday"]
                    for key in required_keys:
                        if key not in setting_value:
                            return json.dumps(
                                {
                                    "error": f"Curfew must contain {', '.join(required_keys)}",
                                    "status": "error",
                                }
                            )
                except json.JSONDecodeError:
                    return json.dumps(
                        {"error": "Invalid JSON format for curfew", "status": "error"}
                    )

            # Update the setting
            user_settings[setting_key] = setting_value
            self.policy.storage.set_user_settings(username, user_settings)

            logger.info(f"Updated {setting_key} for user {username}")
            return json.dumps(
                {
                    "status": "success",
                    "message": f"Updated {setting_key} for user {username}",
                    "username": username,
                    "setting": setting_key,
                    "value": setting_value,
                }
            )
        except Exception as e:
            logger.error(f"Error updating user setting: {e}")
            return json.dumps({"error": str(e)})

    def close(self):
        """
        Closes the IPC socket and removes the socket file.
        """
        self.server.close()
        if os.path.exists(self.socket_path):
            logger.debug(f"Removing socket file on close: {self.socket_path}")
            os.remove(self.socket_path)
