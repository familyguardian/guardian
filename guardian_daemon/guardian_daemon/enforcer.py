"""
Enforcement module for guardian-daemon.
Checks quota and curfew, enforces limits by terminating sessions and blocking logins.
"""

import asyncio
import time
from collections import defaultdict
from typing import Dict, Tuple

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
        # Track last notification times and messages to prevent duplicate notifications
        # Format: {username: {notification_key: (timestamp, remaining_time)}}
        self._last_notifications: Dict[str, Dict[str, Tuple[float, float]]] = (
            defaultdict(dict)
        )
        # Minimum time between similar notifications in seconds
        self._notification_cooldown = 300  # 5 minutes
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

        now = time.time()

        # Use debounced notifications to prevent duplicate messages
        # Notify at 1, 5, and 10 minutes remaining (thresholds in minutes)
        if remaining_time <= 1:
            if self._should_send_notification(username, "1min", remaining_time, now):
                logger.info(f"User {username} has 1 minute left.")
                await self.notify_user(username, "1 minute left!", category="critical")
                self._last_notifications[username]["1min"] = (now, remaining_time)
        elif remaining_time <= 5:
            if self._should_send_notification(username, "5min", remaining_time, now):
                logger.info(f"User {username} has 5 minutes left.")
                await self.notify_user(username, "5 minutes left!", category="warning")
                self._last_notifications[username]["5min"] = (now, remaining_time)
        elif remaining_time <= 10 and remaining_time < total_time / 2:
            if self._should_send_notification(username, "10min", remaining_time, now):
                logger.info(f"User {username} has 10 minutes left.")
                await self.notify_user(username, "10 minutes left!", category="info")
                self._last_notifications[username]["10min"] = (now, remaining_time)
        elif remaining_time <= total_time / 2:
            if self._should_send_notification(username, "50pct", remaining_time, now):
                logger.info(f"User {username} has used 50% of their time.")
                await self.notify_user(
                    username, "50% of your time is used.", category="info"
                )
                self._last_notifications[username]["50pct"] = (now, remaining_time)

    def _should_send_notification(
        self,
        username: str,
        notification_key: str,
        current_remaining_time: float,
        current_time: float,
    ) -> bool:
        """
        Determines if a notification should be sent based on cooldown and time change.

        Args:
            username: The user to check
            notification_key: Type of notification (e.g., "1min", "5min")
            current_remaining_time: Current remaining time in minutes
            current_time: Current timestamp

        Returns:
            bool: True if notification should be sent
        """
        if (
            username not in self._last_notifications
            or notification_key not in self._last_notifications[username]
        ):
            # No previous notification of this type
            return True

        last_time, last_remaining = self._last_notifications[username][notification_key]
        time_elapsed = current_time - last_time

        # Don't send if we're still in cooldown period
        if time_elapsed < self._notification_cooldown:
            logger.debug(
                f"Skipping {notification_key} notification for {username} - cooldown active for {self._notification_cooldown - time_elapsed:.1f}s more"
            )
            return False

        # Send if remaining time has changed significantly (>= 1 minute difference)
        # or if enough time has passed since last notification (>= cooldown period)
        # Send if remaining time has changed significantly (>= 1 minute difference)
        if (
            abs(current_remaining_time - last_remaining) >= 1
            or time_elapsed >= self._notification_cooldown
        ):
            return True

        return False

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
        Implements a debounce mechanism to avoid sending too many similar notifications.
        """
        # Implement debounce mechanism to prevent excessive similar notifications
        if not hasattr(self, "_last_notifications"):
            self._last_notifications = {}

        # Create a key using username + message to track similar notifications
        notification_key = f"{username}:{message}"
        current_time = time.time()

        # If we've sent this notification recently, skip it
        if notification_key in self._last_notifications:
            last_time = self._last_notifications[notification_key]
            # Don't send the same notification more than once every 45 seconds
            if current_time - last_time < 45:
                logger.debug(
                    f"Skipping duplicate notification to {username}: '{message}' (debounced)"
                )
                return

        # Update the last notification time
        self._last_notifications[notification_key] = current_time

        notified = False
        try:
            # Get agent names from the session tracker's cache
            agent_names = self.tracker.get_agent_names_for_user(username)

            if not agent_names:
                logger.warning(f"No running agent found for user {username} in cache.")
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
