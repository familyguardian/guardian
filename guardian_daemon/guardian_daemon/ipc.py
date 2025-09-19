"""
IPC server for admin commands of the Guardian Daemon.
"""

import datetime
import inspect
import json
import os
import socket

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

    def __init__(self, config):
        """
        Initializes the IPC server and opens the Unix socket.

        Args:
            config (dict): Configuration data
        """
        self.policy = Policy()
        self.socket_path = config.get("ipc_socket", "/run/guardian-daemon.sock")
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if os.path.exists(self.socket_path):
            logger.debug(f"Removing existing socket file: {self.socket_path}")
            os.remove(self.socket_path)
        self.server.bind(self.socket_path)
        self.server.listen(1)
        logger.info(f"IPC server started on {self.socket_path}")
        self.handlers = {
            "list_kids": self.handle_list_kids,
            "get_quota": self.handle_get_quota,
            "get_curfew": self.handle_get_curfew,
            "list_timers": self.handle_list_timers,
            "reload_timers": self.handle_reload_timers,
            "reset_quota": self.handle_reset_quota,
            "describe_commands": self.handle_describe_commands,  # <--- NEW
        }

    def serve_once(self):
        """
        Waits for an incoming command, executes the appropriate handler, and sends the response back.
        """
        conn, _ = self.server.accept()
        data = conn.recv(1024).decode().strip()
        logger.debug(f"Received IPC command: {data}")
        cmd, *args = data.split(" ", 1)
        handler = self.handlers.get(cmd)
        if handler:
            arg = args[0] if args else None
            logger.debug(f"Dispatching handler for command: {cmd} with arg: {arg}")
            try:
                response = handler(arg)
                conn.sendall(response.encode())
                logger.debug(f"Sent response: {response}")
            except Exception as e:
                logger.error(f"Error handling command '{cmd}': {e}")
                conn.sendall(b"Error")
        else:
            logger.warning(f"Unknown IPC command: {cmd}")
            conn.sendall(b"Unknown command")
        conn.close()

    def handle_list_kids(self, _):
        """
        Returns the list of all kids (users).
        """
        kids = self.policy.storage.get_all_usernames()
        logger.debug(f"Listing kids: {kids}")
        return json.dumps({"kids": kids})

    def handle_get_quota(self, kid):
        """
        Returns the current quota status of a kid.

        Args:
            kid (str): Username
        """
        if not kid:
            logger.warning("get_quota called without kid argument")
            return json.dumps({"error": "missing kid"})

        tracker = SessionTracker(self.policy, self.config)
        user_policy = self.policy.get_user_policy(kid)
        if user_policy is None:
            logger.warning(f"get_quota: unknown kid '{kid}'")
            return json.dumps({"error": "unknown kid"})
        quota = user_policy.get("daily_quota_minutes")
        if quota is None:
            quota = self.policy.get_default("daily_quota_minutes")

        reset_time = self.policy.data.get("reset_time", "03:00")
        now = datetime.datetime.now(datetime.timezone.utc).astimezone()
        reset_hour, reset_minute = map(int, reset_time.split(":"))
        today_reset = now.replace(
            hour=reset_hour, minute=reset_minute, second=0, microsecond=0
        )
        if now < today_reset:
            last_reset = today_reset - datetime.timedelta(days=1)
        else:
            last_reset = today_reset
        storage = tracker.storage
        sessions = storage.get_sessions_for_user(kid, since=last_reset.timestamp())
        used = sum((s[6] for s in sessions)) / 60  # Minutes
        for session in tracker.active_sessions.values():
            if session["username"] == kid:
                used += (
                    datetime.datetime.now().timestamp() - session["start_time"]
                ) / 60
        remaining = max(0, quota - used)
        logger.debug(
            f"Quota for {kid}: used={used}, limit={quota}, remaining={remaining}"
        )
        return json.dumps(
            {
                "kid": kid,
                "used": round(used, 2),
                "limit": quota,
                "remaining": round(remaining, 2),
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

    def handle_reset_quota(self, _):
        """
        Resets the daily quota for all users (deletes sessions since last reset).
        """

        reset_time = self.policy.data.get("reset_time", "03:00")
        now = datetime.datetime.now(datetime.timezone.utc).astimezone()
        reset_hour, reset_minute = map(int, reset_time.split(":"))
        today_reset = now.replace(
            hour=reset_hour, minute=reset_minute, second=0, microsecond=0
        )
        if now < today_reset:
            last_reset = today_reset - datetime.timedelta(days=1)
        else:
            last_reset = today_reset
        self.policy.storage.delete_sessions_since(last_reset.timestamp())
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
