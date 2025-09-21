"""
Session tracking for guardian-daemon.
Monitors logins/logouts via systemd-logind (DBus), measures usage time and checks quota/curfew.
Stores data in SQLite.
"""

import asyncio
import datetime
import os
import pwd
import time

import yaml
from dbus_next.aio import MessageBus
from dbus_next.constants import BusType
from dbus_next.service import ServiceInterface, method

from guardian_daemon.logging import get_logger
from guardian_daemon.policy import Policy
from guardian_daemon.storage import Storage

logger = get_logger("SessionTracker")


class SessionTracker:
    """
    Monitors and stores user sessions, checks quota and curfew.
    Connects to systemd-logind via DBus.
    """

    def get_total_time(self, username: str) -> float:
        """
        Returns the total usage time (in minutes) for the given user today.
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

        sessions = self.storage.get_sessions_for_user(
            username, since=last_reset.timestamp()
        )
        filtered_sessions = [
            s for s in sessions if s[6] > 30
        ]  # s[6] = duration (seconds)
        total_minutes = sum((s[6] for s in filtered_sessions)) / 60
        for session in self.active_sessions.values():
            if session["username"] == username:
                total_minutes += (time.monotonic() - session["start_time"]) / 60
        return total_minutes

    def get_remaining_time(self, username: str) -> float:
        """
        Returns the remaining allowed time (in minutes) for the given user today.
        """
        user_policy = self.policy.get_user_policy(username)
        if user_policy is None:
            return float("inf")  # Unlimited if not monitored
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

        sessions = self.storage.get_sessions_for_user(
            username, since=last_reset.timestamp()
        )
        filtered_sessions = [
            s for s in sessions if s[6] > 30
        ]  # s[6] = duration (seconds)
        total_minutes = sum((s[6] for s in filtered_sessions)) / 60
        for session in self.active_sessions.values():
            if session["username"] == username:
                total_minutes += (time.monotonic() - session["start_time"]) / 60
        remaining = quota - total_minutes
        return max(0, remaining)

    def receive_lock_event(
        self, session_id: str, username: str, locked: bool, timestamp: float
    ):
        """
        Called via D-Bus/IPC from agent to record lock/unlock events for a session.
        """
        if session_id not in self.active_sessions:
            logger.warning(f"Lock event for unknown session: {session_id}")
            return
        if session_id not in self.session_locks:
            self.session_locks[session_id] = []
        if locked:
            # Lock started
            self.session_locks[session_id].append((timestamp, None))
            logger.debug(f"Session {session_id}: screen locked at {timestamp}")
        else:
            # Lock ended
            # Find last open lock period
            for i in range(len(self.session_locks[session_id]) - 1, -1, -1):
                lock_start, lock_end = self.session_locks[session_id][i]
                if lock_end is None:
                    self.session_locks[session_id][i] = (lock_start, timestamp)
                    logger.debug(
                        f"Session {session_id}: screen unlocked at {timestamp}"
                    )
                    break

    def __init__(self, policy: Policy, config: dict):
        """
        Initialize the SessionTracker with a policy and configuration.

        Args:
            policy (Policy): Policy instance
            config (dict): Parsed configuration
        """
        self.policy = policy
        db_path = config.get("db_path", "guardian.sqlite")
        self.storage = Storage(db_path)
        self.active_sessions: dict[str, dict] = {}
        # Placeholder for lock event tracking (now handled by agent)
        self.session_locks: dict[str, list[tuple[float, float | None]]] = {}

    def handle_login(self, session_id, uid, username, props):
        """
        Register a new session on login for child accounts.
        Also ensure user account is set up: PAM time rules, systemd user service, and agent.

        Args:
            session_id (str): Session ID
            uid (int): User ID
            username (str): Username
        """
        kids = set(self.policy.data.get("users", {}).keys())
        if username not in kids:
            logger.info(
                f"Ignoring session from {username} (UID {uid}) Session {session_id}"
            )
            return
        self.active_sessions[session_id] = {
            "uid": uid,
            "username": username,
            "start_time": time.time(),  # UNIX epoch
        }
        self.session_locks[session_id] = []

        # Ensure user account setup
        from guardian_daemon.user_manager import UserManager

        user_manager = UserManager(self.policy)
        user_manager.write_time_rules()
        user_manager.setup_user_service(username)
        user_manager.ensure_systemd_user_service(username)

        # Optionally start agent for user (if not managed by systemd)
        # os.system(f"runuser -l {username} -c 'guardian_agent &'")

        # Debug output of all info before writing
        desktop = props.get("Desktop", None)
        service = props.get("Service", None)

        # Create session entry with end_time and duration=0
        self.storage.add_session(
            session_id,
            username,
            uid,
            self.active_sessions[session_id]["start_time"],
            0.0,
            0.0,
            desktop,
            service,
        )
        logger.info(f"Login: {username} (UID {uid}) Session {session_id}")

    def handle_logout(self, session_id):
        """
        End a session on logout and save it in the database for child accounts.

        Args:
            session_id (str): Session ID
        """
        session = self.active_sessions.pop(session_id, None)
        lock_periods = self.session_locks.pop(session_id, [])
        if session:
            kids = set(self.policy.data.get("users", {}).keys())
            if session["username"] not in kids:
                logger.info(
                    f"Ignoring logout from {session['username']} Session {session_id}"
                )
                return
            end_time = time.monotonic()
            duration = end_time - session["start_time"]
            # Deduct locked time
            locked_time = 0.0
            for lock_start, lock_end in lock_periods:
                if lock_end is not None:
                    locked_time += max(0.0, lock_end - lock_start)
                else:
                    locked_time += max(0.0, end_time - lock_start)
            effective_duration = duration - locked_time
            logger.debug(
                f"Session {session_id}: raw duration={duration:.1f}s, locked={locked_time:.1f}s, effective={effective_duration:.1f}s"
            )
            # Update session entry
            self.storage.update_session_logout(session_id, end_time, effective_duration)
            logger.info(
                f"Logout: {session['username']} Session {session_id} Duration: {effective_duration:.1f}s (locked: {locked_time:.1f}s)"
            )

    def check_quota(self, username: str) -> bool:
        """
        Sum all sessions since the last reset and check against the daily quota.
        Returns True if time remains, otherwise False.

        Args:
            username (str): Username

        Returns:
            bool: True if time remains, False if limit reached
        """
        user_policy = self.policy.get_user_policy(username)
        if user_policy is None:
            return True  # Nutzer wird nicht überwacht
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

        sessions = self.storage.get_sessions_for_user(
            username, since=last_reset.timestamp()
        )
        # Filter: Only sessions with meaningful duration (> 0.5 min) and optionally no SDDM/service logins
        filtered_sessions = [
            s for s in sessions if s[6] > 30
        ]  # s[6] = duration (seconds), >30s
        total_minutes = sum((s[6] for s in filtered_sessions)) / 60

        for session in self.active_sessions.values():
            if session["username"] == username:
                total_minutes += (time.time() - session["start_time"]) / 60

        return total_minutes < quota

    async def run(self):
        """
        Start session tracking, connect to systemd-logind via DBus, and listen for KDE lock events.
        Also checks for already logged-in child sessions on startup.
        """
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        # Request and hold the org.guardian.Daemon name
        await bus.request_name("org.guardian.Daemon")
        # Export D-Bus interface for agent lock events
        daemon_iface = GuardianDaemonInterface(self)
        bus.export("/org/guardian/Daemon", daemon_iface)
        introspection = await bus.introspect(
            "org.freedesktop.login1", "/org/freedesktop/login1"
        )
        obj = bus.get_proxy_object(
            "org.freedesktop.login1", "/org/freedesktop/login1", introspection
        )
        manager = obj.get_interface("org.freedesktop.login1.Manager")

        # Check for already logged-in child sessions on startup
        sessions = await manager.call_list_sessions()
        kids = set(self.policy.data.get("users", {}).keys())
        for session_info in sessions:
            # session_info: (session_id, uid, user, seat, object_path)
            session_id, uid, username, seat, object_path = session_info
            if username in kids:
                try:
                    session_obj = bus.get_proxy_object(
                        "org.freedesktop.login1",
                        object_path,
                        await bus.introspect("org.freedesktop.login1", object_path),
                    )
                    session_iface = session_obj.get_interface(
                        "org.freedesktop.login1.Session"
                    )
                    # Gather properties
                    props = {}
                    introspection = await bus.introspect(
                        "org.freedesktop.login1", object_path
                    )
                    session_interface = next(
                        (
                            iface
                            for iface in introspection.interfaces
                            if iface.name == "org.freedesktop.login1.Session"
                        ),
                        None,
                    )
                    if session_interface:
                        property_names = [p.name for p in session_interface.properties]
                        for prop in property_names:
                            getter = getattr(session_iface, f"get_{prop.lower()}", None)
                            if getter:
                                try:
                                    props[prop] = await getter()
                                except Exception as e:
                                    props[prop] = f"[ERROR: {e}]"
                            else:
                                props[prop] = "[NO GETTER]"
                    else:
                        props = {
                            "error": "Session interface not found in introspection"
                        }
                    # Run setup for already logged-in child
                    logger.info(
                        f"Startup: found active child session {session_id} for {username}"
                    )
                    self.handle_login(session_id, uid, username, props)
                except Exception as e:
                    logger.error(
                        f"Error processing existing session {session_id} for {username}: {e}"
                    )

        async def get_session_info(object_path):
            try:
                introspection = await bus.introspect(
                    "org.freedesktop.login1", object_path
                )
            except Exception as e:
                logger.warning(
                    f"Could not introspect session object {object_path}: {e}"
                )
                return None
            session_obj = bus.get_proxy_object(
                "org.freedesktop.login1",
                object_path,
                introspection,
            )
            session_iface = session_obj.get_interface("org.freedesktop.login1.Session")
            # Username
            username = await session_iface.get_name()
            # UID
            user_struct = await session_iface.get_user()
            uid = (
                user_struct[0]
                if isinstance(user_struct, (list, tuple))
                else user_struct
            )
            return username, uid

        def session_new_handler(session_id, object_path):
            """
            Handle new session event.
            """

            async def inner():
                # Session-Objekt holen
                session_obj = bus.get_proxy_object(
                    "org.freedesktop.login1",
                    object_path,
                    await bus.introspect("org.freedesktop.login1", object_path),
                )
                session_iface = session_obj.get_interface(
                    "org.freedesktop.login1.Session"
                )
                # Alle Properties dynamisch auslesen
                props = {}
                introspection = await bus.introspect(
                    "org.freedesktop.login1", object_path
                )
                session_interface = next(
                    (
                        iface
                        for iface in introspection.interfaces
                        if iface.name == "org.freedesktop.login1.Session"
                    ),
                    None,
                )
                if session_interface:
                    property_names = [p.name for p in session_interface.properties]
                    for prop in property_names:
                        getter = getattr(session_iface, f"get_{prop.lower()}", None)
                        if getter:
                            try:
                                props[prop] = await getter()
                            except Exception as e:
                                props[prop] = f"[ERROR: {e}]"
                        else:
                            props[prop] = "[NO GETTER]"
                else:
                    props = {"error": "Session interface not found in introspection"}
                # Hole Name und User explizit
                username = props.get("Name", None)
                user_struct = props.get("User", None)
                uid = (
                    user_struct[0]
                    if isinstance(user_struct, (list, tuple))
                    else user_struct
                )
                kids = set(self.policy.data.get("users", {}).keys())
                if username not in kids:
                    logger.info(
                        f"Ignoring session from {username} (UID {uid}) Session {session_id}"
                    )
                    return
                # print(f"[DEBUG] Alle Session-Properties für {session_id}: {props}")
                logger.debug(
                    f"Extracted: Name={username}, UID={uid}, Desktop={props.get('Desktop')}, Service={props.get('Service')}"
                )
                self.handle_login(session_id, uid, username, props)

            asyncio.create_task(inner())

        def session_removed_handler(session_id, object_path):
            # Keine D-Bus-Abfrage mehr, sondern lokale Daten nutzen
            self.handle_logout(session_id)

        manager.on_session_new(session_new_handler)
        manager.on_session_removed(session_removed_handler)

        logger.info("SessionTracker running. Waiting for logins/logouts...")
        while True:
            await asyncio.sleep(3600)

    def _get_username(self, uid):
        """
        Get the username for a given UID.

        Args:
            uid (int): User ID

        Returns:
            str: Username
        """

        try:
            return pwd.getpwuid(uid).pw_name
        except Exception:
            return str(uid)

    def perform_daily_reset(self):
        """
        Perform daily reset: delete sessions since last reset and reset quotas if needed.
        """
        # Get last reset timestamp
        last_reset = self.storage.get_last_reset_timestamp()
        if last_reset is not None:
            self.storage.delete_sessions_since(last_reset)
        # Optionally, reset any in-memory quota tracking here
        logger.info("Performed daily session reset.")

    def get_active_users(self) -> list:
        """
        Return a list of currently active usernames.
        """
        return [session["username"] for session in self.active_sessions.values()]


class GuardianDaemonInterface(ServiceInterface):
    def __init__(self, session_tracker):
        super().__init__("org.guardian.Daemon")
        self.session_tracker = session_tracker

    @method()
    def LockEvent(
        self, session_id: "s", username: "s", locked: "b", timestamp: "d"  # noqa: F821
    ):
        """
        Receives lock/unlock events from agents and forwards to SessionTracker.
        """
        import inspect

        # Try to get sender from D-Bus context if available
        sender = None
        try:
            frame = inspect.currentframe()
            while frame:
                if "message" in frame.f_locals:
                    sender = getattr(frame.f_locals["message"], "sender", None)
                    break
                frame = frame.f_back
        except Exception:
            sender = None
        logger.info(
            f"Received LockEvent: session={session_id} user={username} locked={locked} ts={timestamp} sender={sender}"
        )
        # If session is unknown, log and pause time tracking for locked=True
        if session_id not in self.session_tracker.active_sessions:
            logger.warning(
                f"Lock event for unknown session: {session_id} from sender={sender} user={username}"
            )
            # Optionally, pause time tracking for this user
            if locked:
                logger.info(
                    f"Pausing time tracking for user {username} due to lock event from unknown session."
                )
                self.session_tracker.pause_user_time(username, timestamp)
            return None
        self.session_tracker.receive_lock_event(session_id, username, locked, timestamp)
        return None

    def pause_user_time(self, username, timestamp):
        """
        Pause time tracking for a user when a lock event is received for an unknown session.
        """
        # Implementation: mark user as locked, store timestamp
        if not hasattr(self, "user_locks"):
            self.user_locks = {}
        self.user_locks[username] = timestamp
        logger.info(f"User {username} time tracking paused at {timestamp}.")


if __name__ == "__main__":
    # Load default configuration
    base_dir = os.path.dirname(os.path.abspath(__file__))
    default_config_path = os.path.join(base_dir, "../default-config.yaml")
    with open(default_config_path, "r") as f:
        config = yaml.safe_load(f)
    # Override with values from config.yaml if present
    config_path = os.path.join(base_dir, "../config.yaml")
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            user_config = yaml.safe_load(f)
        if user_config:
            config.update(user_config)
    # If config.yaml is missing, just use default config
    policy = Policy(config_path if os.path.exists(config_path) else default_config_path)
    tracker = SessionTracker(policy, config)
    asyncio.run(tracker.run())
# logind watcher
