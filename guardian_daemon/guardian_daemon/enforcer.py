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
        logger.info(f"Enforcing quota/curfew for user: {username}")
        remaining_time = self.tracker.get_remaining_time(username)
        total_time = self.tracker.get_total_time(username)

        if remaining_time <= 0:
            logger.info(
                f"User {username} has exceeded daily quota. Starting grace period."
            )
            self.notify_user(
                username, "Time over! Grace period starts now.", category="critical"
            )
            self.handle_grace_period(username)
            return

        if remaining_time <= 60:
            logger.info(f"User {username} has 1 minute left.")
            self.notify_user(username, "1 minute left!", category="critical")
        elif remaining_time <= 300:
            logger.info(f"User {username} has 5 minutes left.")
            self.notify_user(username, "5 minutes left!", category="warning")
        elif remaining_time <= 600 and remaining_time < total_time / 2:
            logger.info(f"User {username} has 10 minutes left.")
            self.notify_user(username, "10 minutes left!", category="info")
        elif remaining_time <= total_time / 2:
            logger.info(f"User {username} has used 50% of their time.")
            self.notify_user(username, "50% of your time is used.", category="info")

    async def handle_grace_period(self, username):
        """
        Handles the grace period by notifying the user every minute until time is up.
        """
        grace_time = self.policy.get_grace_time(username)
        logger.info(f"Grace period for user {username}: {grace_time} minutes.")
        while grace_time > 0:
            self.notify_user(
                username,
                f"{grace_time} minutes of grace time left! Save your work.",
                category="critical",
            )
            logger.info(f"User {username} grace time left: {grace_time} minutes.")
            grace_time -= 1
            import asyncio

            await asyncio.sleep(1)

        self.terminate_session(username)
        self.notify_user(
            username, "Session terminated due to time over.", category="critical"
        )
        logger.info(f"User {username} session terminated after grace period.")

    def terminate_session(self, username):
        """
        Terminates all running desktop sessions of the user (via systemd loginctl).
        Only sessions with a desktop environment (not systemd-user/service) are targeted.
        """
        import subprocess

        try:
            # Get all sessions for the user
            result = subprocess.run(
                ["loginctl", "list-sessions", "--no-legend"],
                capture_output=True,
                text=True,
                check=True,
            )
            sessions = []
            for line in result.stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 3 and parts[2] == username:
                    session_id = parts[0]
                    # Try to get session details from tracker
                    session_info = self.tracker.active_sessions.get(session_id)
                    service = session_info["service"] if session_info else None
                    desktop = session_info["desktop"] if session_info else None
                    logger.info(
                        f"Found session: id={session_id}, service={service}, desktop={desktop}, username={username}"
                    )
                    # Only terminate if desktop is set and service is not systemd-user
                    if desktop and service != "systemd-user":
                        sessions.append(session_id)
                    else:
                        logger.info(
                            f"Skipping session {session_id}: not a desktop session (service={service}, desktop={desktop})"
                        )
            if not sessions:
                logger.warning(
                    f"No active desktop sessions found for {username} to terminate."
                )
                return
            for session_id in sessions:
                try:
                    subprocess.run(
                        ["loginctl", "terminate-session", session_id], check=True
                    )
                    logger.info(
                        f"Terminated desktop session {session_id} for user {username}."
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to terminate session {session_id} for user {username}: {e}"
                    )
        except Exception as e:
            logger.error(f"Error terminating sessions for {username}: {e}")

    def notify_user(self, username, message, category="info"):
        """
        Sends a desktop notification to all matching agents of the given user (via D-Bus).
        Uses cached agent_name_map from SessionTracker first, falls back to discover_agent_names_for_user if needed.
        """
        import asyncio

        from dbus_next import DBusError
        from dbus_next.aio import MessageBus
        from dbus_next.constants import BusType

        async def send():
            bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            notified = False
            # Try cached agent names first
            agent_names = self.tracker.get_agent_names_for_user(username)
            if not agent_names:
                logger.debug(
                    f"No cached agent D-Bus names for user {username}, will try discovery if needed."
                )
            for agent_name in agent_names:
                for obj_path in ["/org/guardian/Agent"] + [
                    f"/org/guardian/Agent{i}" for i in range(1, 10)
                ]:
                    try:
                        proxy = await bus.introspect(agent_name, obj_path)
                        obj = bus.get_proxy_object(agent_name, obj_path, proxy)
                        iface = obj.get_interface("org.guardian.Agent")
                        agent_username = await iface.call_get_username()
                        session_info = None
                        for s in self.tracker.active_sessions.values():
                            if (
                                s.get("agent_path") == obj_path
                                and s["username"] == username
                            ):
                                session_info = s
                                break
                        service = session_info["service"] if session_info else None
                        desktop = session_info["desktop"] if session_info else None
                        if service == "systemd-user" or not desktop:
                            logger.debug(
                                f"Skipping notification for agent {agent_name} at {obj_path}: service={service}, desktop={desktop}"
                            )
                            continue
                        if agent_username == username:
                            await iface.call_notify_user(message, category)
                            logger.info(
                                f"Message sent to Agent {agent_name} at {obj_path} for user {username}."
                            )
                            notified = True
                    except DBusError:
                        continue
                    except Exception as e:
                        logger.error(
                            f"Notify error for Agent {agent_name} at {obj_path}: {e}"
                        )
            # If not notified, fall back to discovery
            if not notified:
                logger.info(
                    f"No notification sent using cached mapping for {username}, trying discovery."
                )
                agent_names = await self.tracker.discover_agent_names_for_user(username)
                for agent_name in agent_names:
                    for obj_path in ["/org/guardian/Agent"] + [
                        f"/org/guardian/Agent{i}" for i in range(1, 10)
                    ]:
                        try:
                            proxy = await bus.introspect(agent_name, obj_path)
                            obj = bus.get_proxy_object(agent_name, obj_path, proxy)
                            iface = obj.get_interface("org.guardian.Agent")
                            agent_username = await iface.call_get_username()
                            session_info = None
                            for s in self.tracker.active_sessions.values():
                                if (
                                    s.get("agent_path") == obj_path
                                    and s["username"] == username
                                ):
                                    session_info = s
                                    break
                            service = session_info["service"] if session_info else None
                            desktop = session_info["desktop"] if session_info else None
                            if service == "systemd-user" or not desktop:
                                logger.debug(
                                    f"Skipping notification for agent {agent_name} at {obj_path}: service={service}, desktop={desktop}"
                                )
                                continue
                            if agent_username == username:
                                await iface.call_notify_user(message, category)
                                logger.info(
                                    f"Message sent to Agent {agent_name} at {obj_path} for user {username}."
                                )
                                notified = True
                        except DBusError:
                            continue
                        except Exception as e:
                            logger.error(
                                f"Notify error for Agent {agent_name} at {obj_path}: {e}"
                            )
            if not notified:
                logger.warning(f"No Agent for user {username} reachable.")

        try:
            asyncio.create_task(send())
        except Exception as e:
            logger.error(f"Notify error for {username}: {message} ({e})")


# Quota/Curfew enforcement
