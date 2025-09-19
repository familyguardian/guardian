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

from guardian_daemon.logging import get_logger
from guardian_daemon.policy import Policy
from guardian_daemon.storage import Storage

logger = get_logger("SessionTracker")


class SessionTracker:
    """
    Monitors and stores user sessions, checks quota and curfew.
    Connects to systemd-logind via DBus.
    """

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
            "start_time": time.monotonic(),
        }
        self.session_locks[session_id] = []
        # Debug output of all info before writing
        desktop = props.get("Desktop", None)
        service = props.get("Service", None)
        # print(
        #     f"[DEBUG] Writing session to DB: session_id={session_id}, username={username}, uid={uid}, start_time={self.active_sessions[session_id]['start_time']}, end_time=0.0, duration=0.0, desktop={desktop}, service={service}"
        # )
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
                total_minutes += (time.monotonic() - session["start_time"]) / 60

        return total_minutes < quota

    async def run(self):
        """
        Start session tracking, connect to systemd-logind via DBus, and listen for KDE lock events.
        """

        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        introspection = await bus.introspect(
            "org.freedesktop.login1", "/org/freedesktop/login1"
        )
        obj = bus.get_proxy_object(
            "org.freedesktop.login1", "/org/freedesktop/login1", introspection
        )
        manager = obj.get_interface("org.freedesktop.login1.Manager")

        async def get_session_info(object_path):
            session_obj = bus.get_proxy_object(
                "org.freedesktop.login1",
                object_path,
                await bus.introspect("org.freedesktop.login1", object_path),
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
                    print(
                        f"Ignoriere Session von {username} (UID {uid}) Session {session_id}"
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
    policy = Policy(config_path)
    tracker = SessionTracker(policy, config)
    asyncio.run(tracker.run())
# logind watcher
