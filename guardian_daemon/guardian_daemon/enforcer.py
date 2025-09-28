"""
Enforcement module for guardian-daemon.
Checks quota and curfew, enforces limits by terminating sessions and blocking logins.
"""

import asyncio

from dbus_next import DBusError
from dbus_next.aio import MessageBus
from dbus_next.constants import BusType

from guardian_daemon.logging import get_logger
from guardian_daemon.policy import Policy
from guardian_daemon.sessions import SessionTracker

logger = get_logger("Enforcer")


class Enforcer:
    """
    Enforcement logic for quota and curfew. Handles session termination and user notifications.
    """

    def __init__(self, policy: Policy, tracker: SessionTracker):
        """
        Initialize the Enforcer with a policy and session tracker.
        """
        self.policy = policy
        self.tracker = tracker
        self._grace_period_users = set()

    async def enforce_user(self, username):
        """
        Checks quota and curfew for a user and enforces actions if necessary.
        """
        if username in self._grace_period_users:
            logger.debug(
                f"Grace period already active for {username}, skipping enforcement check."
            )
            return

        logger.info(f"Enforcing quota/curfew for user: {username}")
        remaining_time = await self.tracker.get_remaining_time(username)
        total_time = await self.tracker.get_total_time(username)

        if remaining_time <= 0:
            logger.info(
                f"User {username} has exceeded daily quota. Starting grace period."
            )
            await self.notify_user(
                username, "Time over! Grace period starts now.", category="critical"
            )
            self._grace_period_users.add(username)
            try:
                await self.handle_grace_period(username)
            finally:
                self._grace_period_users.remove(username)
            return

        if remaining_time <= 60:
            logger.info(f"User {username} has 1 minute left.")
            await self.notify_user(username, "1 minute left!", category="critical")
        elif remaining_time <= 300:
            logger.info(f"User {username} has 5 minutes left.")
            await self.notify_user(username, "5 minutes left!", category="warning")
        elif remaining_time <= 600 and remaining_time < total_time / 2:
            logger.info(f"User {username} has 10 minutes left.")
            await self.notify_user(username, "10 minutes left!", category="info")
        elif remaining_time <= total_time / 2:
            logger.info(f"User {username} has used 50% of their time.")
            await self.notify_user(
                username, "50% of your time is used.", category="info"
            )

    async def handle_grace_period(self, username):
        """
        Handles the grace period by notifying the user every minute until time is up.
        """
        grace_time = self.policy.get_grace_time(username)
        logger.info(f"Grace period for user {username}: {grace_time} minutes.")
        while grace_time > 0:
            await self.notify_user(
                username,
                f"{grace_time} minutes of grace time left! Save your work.",
                category="critical",
            )
            logger.info(f"User {username} grace time left: {grace_time} minutes.")
            grace_time -= 1
            await asyncio.sleep(60)

        await self.terminate_session(username)
        await self.notify_user(
            username, "Session terminated due to time over.", category="critical"
        )
        logger.info(f"User {username} session terminated after grace period.")

    async def terminate_session(self, username):
        """
        Terminates all running desktop sessions of the user (via systemd loginctl).
        Only sessions with a desktop environment (not systemd-user/service) are targeted.
        """
        import asyncio

        logger.info(f"Attempting to terminate sessions for user {username}")
        try:
            # Get all sessions for the user
            proc = await asyncio.create_subprocess_exec(
                "loginctl",
                "list-sessions",
                "--no-legend",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                logger.error(f"loginctl list-sessions failed: {stderr.decode()}")
                return

            sessions_to_terminate = []
            lines = stdout.decode().strip().split("\n")

            async with self.tracker.session_lock:
                for line in lines:
                    parts = line.split()
                    if len(parts) >= 3 and parts[2] == username:
                        session_id = parts[0]
                        session_info = self.tracker.active_sessions.get(session_id)
                        if session_info:
                            service = session_info.get("service")
                            desktop = session_info.get("desktop")
                            logger.info(
                                f"Found session: id={session_id}, service={service}, desktop={desktop}, username={username}"
                            )
                            # Only terminate if desktop is set and service is not systemd-user
                            if desktop and service != "systemd-user":
                                sessions_to_terminate.append(session_id)
                            else:
                                logger.info(
                                    f"Skipping session {session_id}: not a desktop session (service={service}, desktop={desktop})"
                                )
                        else:
                            logger.warning(
                                f"Session {session_id} not found in tracker, cannot check type. Skipping."
                            )

            if not sessions_to_terminate:
                logger.warning(
                    f"No active desktop sessions found for {username} to terminate."
                )
                return

            for session_id in sessions_to_terminate:
                logger.info(f"Terminating session {session_id} for user {username}")
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "loginctl",
                        "terminate-session",
                        session_id,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, stderr = await proc.communicate()
                    if proc.returncode == 0:
                        logger.info(
                            f"Successfully terminated desktop session {session_id} for user {username}."
                        )
                    else:
                        logger.error(
                            f"Failed to terminate session {session_id} for user {username}: {stderr.decode()}"
                        )
                except Exception as e:
                    logger.error(
                        f"Exception while terminating session {session_id} for user {username}: {e}"
                    )
        except Exception as e:
            logger.error(f"Error terminating sessions for {username}: {e}")

    async def notify_user(self, username, message, category="info"):
        """
        Sends a desktop notification to all matching agents of the given user (via D-Bus).
        """
        notified = False
        try:
            agent_names = await self.tracker.discover_agent_names_for_user(username)
            if not agent_names:
                logger.warning(
                    f"No running agent found for user {username} during discovery."
                )
                return

            bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

            for agent_name in agent_names:
                try:
                    # The object path is now fixed and unique per agent instance
                    obj_path = "/org/guardian/Agent"
                    proxy = await bus.introspect(agent_name, obj_path)
                    obj = bus.get_proxy_object(agent_name, obj_path, proxy)
                    iface = obj.get_interface("org.guardian.Agent")

                    # We can trust the agent since discovery is based on the user's own session bus
                    await iface.call_notify_user(message, category)
                    logger.info(
                        f"Message sent to Agent {agent_name} for user {username}."
                    )
                    notified = True
                except DBusError as e:
                    logger.warning(
                        f"D-Bus error while trying to notify agent {agent_name}: {e}. It might have terminated."
                    )
                except Exception as e:
                    logger.error(f"Unexpected error notifying agent {agent_name}: {e}")

            if not notified:
                logger.warning(
                    f"Could not send notification to any agent for user {username}."
                )

        except Exception as e:
            logger.error(f"Failed to send notification to {username}: {e}")


# Quota/Curfew enforcement
