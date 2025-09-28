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
        peer_uid, peer_gid, _ = peer_creds

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

        Args:
            kid (str): Username
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
                "used": round(used_time / 60, 2),  # minutes
                "limit": round(total_time / 60, 2),  # minutes
                "remaining": round(remaining_time / 60, 2),  # minutes
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

    def handle_describe_commands(self, _):
        """
        Returns a description of all available IPC commands and their parameters as JSON.
        """
        commands = {}
        for cmd, handler in self.handlers.items():
            desc = handler.__doc__.strip() if handler.__doc__ else ""

            sig = inspect.signature(handler)
            params = [
                p.name
                for p in sig.parameters.values()
                if p.name != "self" and p.name != "_"
            ]
            commands[cmd] = {"description": desc, "params": params}
        logger.debug(f"Describing IPC commands: {list(commands.keys())}")
        return json.dumps(commands)

    def close(self):
        """
        Closes the IPC socket and removes the socket file.
        """
        self.server.close()
        if os.path.exists(self.socket_path):
            logger.debug(f"Removing socket file on close: {self.socket_path}")
            os.remove(self.socket_path)
