"""
Enforcement module for guardian-daemon.
Checks quota and curfew, enforces limits by terminating sessions and blocking logins.
"""

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

    def enforce_user(self, username):
        """
        Checks quota and curfew for a user and enforces actions if necessary.
        """
        # Quota enforcement
        if not self.tracker.check_quota(username):
            self.notify_user(username, "Quota reached! Your grace period begins.")
            # TODO: Grace-minutes timer/countdown
            # TODO: Notification before grace period ends
            # After grace period:
            self.terminate_session(username)
            self.notify_user(username, "Your session will now be terminated.")

        # Curfew enforcement (optional, e.g. via PAMManager)
        # TODO: Curfew check and block login if necessary

    def terminate_session(self, username):
        """
        Terminates all running sessions of the user (e.g. via systemd or loginctl).
        """
        # TODO: Integration with systemd/loginctl
        logger.warning(f"Terminating all sessions for {username}")

    def notify_user(self, username, message, category="info"):
        """
        Sends a desktop notification to all matching agents of the given user (via D-Bus).
        """
        try:
            import asyncio

            from dbus_next import DBusError
            from dbus_next.aio import MessageBus

            async def send():
                bus = await MessageBus().connect()
                # Enumerate all possible agent instances (e.g., per session)
                # For simplicity, try common session paths and ignore errors
                notified = False
                for session_num in range(1, 10):
                    obj_path = (
                        f"/org/guardian/Agent{session_num}"
                        if session_num > 1
                        else "/org/guardian/Agent"
                    )
                    try:
                        proxy = await bus.introspect("org.guardian.Agent", obj_path)
                        obj = bus.get_proxy_object(
                            "org.guardian.Agent", obj_path, proxy
                        )
                        iface = obj.get_interface("org.guardian.Agent")
                        agent_username = await iface.call_get_username()
                        if agent_username == username:
                            await iface.call_notify_user(message, category)
                            logger.info(
                                f"Message sent to Agent {obj_path} for user {username}."
                            )
                            notified = True
                    except DBusError:
                        continue
                    except Exception as e:
                        logger.error(f"Notify error for Agent {obj_path}: {e}")
                if not notified:
                    logger.warning(f"No Agent for user {username} reachable.")

            asyncio.run(send())
        except Exception as e:
            logger.error(f"Notify error for {username}: {message} ({e})")


# Quota/Curfew enforcement
