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
from guardian_daemon.user_manager import UserManager

logger = get_logger("SessionTracker")


class SessionTracker:
    """
    Monitors and stores user sessions, checks quota and curfew.
    Connects to systemd-logind via DBus.
    """

    async def refresh_agent_name_mapping(self):
        """
        Refresh the mapping of usernames to their current D-Bus agent names using discover_agent_names_for_user().
        Stores the mapping in self.agent_name_map: {username: [dbus_name, ...]}
        """
        self.agent_name_map = {}
        kids = set(self.policy.data.get("users", {}).keys())
        for username in kids:
            try:
                names = await self.discover_agent_names_for_user(username)
                self.agent_name_map[username] = names
                logger.debug(f"Refreshed agent D-Bus names for {username}: {names}")
            except Exception as e:
                logger.error(f"Error refreshing agent names for {username}: {e}")

    def get_agent_names_for_user(self, username: str) -> list:
        """
        Return the cached list of D-Bus agent names for a user, or empty list if not found.
        """
        if hasattr(self, "agent_name_map"):
            return self.agent_name_map.get(username, [])
        return []

    async def discover_agent_names_for_user(self, username: str) -> list:
        """
        Discover current org.guardian.Agent D-Bus names for the given user by listing names on the system bus.
        Returns a list of matching D-Bus names.
        """
        from dbus_next.aio import MessageBus
        from dbus_next.constants import BusType

        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        # Use the standard D-Bus interface to list names
        introspection = await bus.introspect(
            "org.freedesktop.DBus", "/org/freedesktop/DBus"
        )
        obj = bus.get_proxy_object(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", introspection
        )
        iface = obj.get_interface("org.freedesktop.DBus")
        names = await iface.call_list_names()
        # Filter for agent prefix and username
        return [
            name
            for name in names
            if name.startswith("org.guardian.Agent") and username in name
        ]

    def get_agent_paths_for_user(self, username: str):
        """
        Returns a list of D-Bus object paths for agents belonging to the given user.
        This should be tracked in active_sessions as 'agent_path' if available, otherwise default to /org/guardian/Agent or numbered agents.
        """
        # If agent_path is tracked in session, collect all for user
        paths = []
        for session in self.active_sessions.values():
            if session["username"] == username:
                agent_path = session.get("agent_path")
                if agent_path:
                    paths.append(agent_path)
        # Fallback: try default paths if none found
        if not paths:
            # Try default and numbered agent paths
            paths = [f"/org/guardian/Agent{i}" for i in range(1, 10)]
            paths.insert(0, "/org/guardian/Agent")
        return paths

    def get_user_sessions(self, username: str):
        """
        Returns a list of active session details for the given user.
        Each item is a dict with session_id, service, desktop, start_time, etc.
        """
        return [
            {"session_id": sid, **session}
            for sid, session in self.active_sessions.items()
            if session["username"] == username
        ]

    async def _calculate_used_time(self, username: str) -> float:
        """
        Calculates the total used time for a user since the last reset.
        This is an internal method and should be called within a lock.
        """
        async with self.session_lock:
            # First, calculate time for currently active sessions for the user
            active_duration_seconds = 0
            now = time.time()
            for session in self.active_sessions.values():
                if session["username"] == username:
                    active_duration_seconds += now - session["start_time"]

            # Then, add the duration of already completed sessions from the database
            reset_time = self.policy.data.get("reset_time", "03:00")
            now_dt = datetime.datetime.now(datetime.timezone.utc).astimezone()
            reset_hour, reset_minute = map(int, reset_time.split(":"))
            today_reset = now_dt.replace(
                hour=reset_hour, minute=reset_minute, second=0, microsecond=0
            )
            if now_dt < today_reset:
                last_reset = today_reset - datetime.timedelta(days=1)
            else:
                last_reset = today_reset

            db_sessions = self.storage.get_sessions_for_user(
                username, since=last_reset.timestamp()
            )
            # Filter: Only sessions with meaningful duration and not systemd-user sessions
            filtered_sessions = [
                s for s in db_sessions if s[6] > 30 and s[8] != "systemd-user"
            ]
            db_duration_seconds = sum(s[6] for s in filtered_sessions)

            total_seconds = active_duration_seconds + db_duration_seconds
            return total_seconds / 60

    async def get_total_time(self, username: str) -> float:
        """
        Returns the total allowed time (in minutes) for the given user today.
        """
        user_policy = self.policy.get_user_policy(username)
        if user_policy is None:
            return float("inf")  # Unlimited if not monitored
        quota = user_policy.get("daily_quota_minutes")
        if quota is None:
            quota = self.policy.get_default("daily_quota_minutes")
        return float(quota)

    async def get_remaining_time(self, username: str) -> float:
        """
        Returns the remaining allowed time (in minutes) for the given user today.
        """
        total_allowed = await self.get_total_time(username)
        if total_allowed == float("inf"):
            return float("inf")

        used_minutes = await self._calculate_used_time(username)
        remaining = total_allowed - used_minutes
        return max(0, remaining)

    async def receive_lock_event(
        self, session_id: str, username: str, locked: bool, timestamp: float
    ):
        """
        Called via D-Bus/IPC from agent to record lock/unlock events for a session.
        Also updates session progress in the database.
        """
        async with self.session_lock:
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
            # No longer updating DB here; periodic task handles it.

    async def periodic_session_update(self, interval: int = 60):
        """
        Periodically update all active sessions in the database with current duration.
        """
        while True:
            now = time.time()
            async with self.session_lock:
                for session_id, session in self.active_sessions.items():
                    duration = now - session["start_time"]
                    self.storage.update_session_progress(session_id, duration)
            await asyncio.sleep(interval)

    def __init__(self, policy: Policy, config: dict, user_manager: UserManager):
        """
        Initialize the SessionTracker with a policy and configuration.

        Args:
            policy (Policy): Policy instance
            config (dict): Parsed configuration
            user_manager (UserManager): An instance of the user manager.
        """
        self.policy = policy
        self.user_manager = user_manager
        db_path = config.get("db_path", "guardian.sqlite")
        self.storage = Storage(db_path)
        self.active_sessions: dict[str, dict] = {}
        self.session_locks: dict[str, list[tuple[float, float | None]]] = {}
        self.session_lock = asyncio.Lock()

        # Restore active sessions from database (sessions with no end_time)
        self._restore_active_sessions()

    def _restore_active_sessions(self):
        """
        Restore sessions that are still open (no end_time) from the database into active_sessions.
        The start_time is reset to now to avoid counting offline time.
        """
        c = self.storage.conn.cursor()
        c.execute(
            "SELECT session_id, username, uid, start_time, desktop, service FROM sessions WHERE end_time IS NULL OR end_time = 0"
        )
        rows = c.fetchall()
        now = time.time()
        for row in rows:
            session_id, username, uid, old_start_time, desktop, service = row
            # The session was active before a restart. Reset start_time to now
            # to ensure we only count time from when the daemon is actually running.
            # The time before the restart is already persisted by periodic_session_update.
            self.active_sessions[session_id] = {
                "uid": uid,
                "username": username,
                "start_time": now,
                "desktop": desktop,
                "service": service,
            }
            self.session_locks[session_id] = []
            logger.info(
                f"Restored session {session_id} for {username}. Original start: {old_start_time}, new effective start: {now}"
            )

        if rows:
            logger.info(
                f"Restored {len(rows)} active sessions from database on startup."
            )

    async def handle_login(self, session_id, uid, username, props):
        """
        Register a new session on login for child accounts.
        Skips systemd-user sessions.
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
        desktop = props.get("Desktop", None)
        service = props.get("Service", None)
        if service == "systemd-user":
            logger.info(
                f"Ignoring systemd-user session: {session_id} for user {username}"
            )
            return

        async with self.session_lock:
            self.active_sessions[session_id] = {
                "uid": uid,
                "username": username,
                "start_time": time.time(),  # UNIX epoch
                "desktop": desktop,
                "service": service,
            }
            self.session_locks[session_id] = []

        # Ensure user account setup - user already exists since we have a login event
        self.user_manager.write_time_rules()

        # Setup user service and ensure it's running
        # We know the user exists because we have a login event
        self.user_manager.setup_user_service(username)
        self.user_manager.ensure_systemd_user_service(username)

        # Optionally start agent for user (if not managed by systemd)
        # os.system(f"runuser -l {username} -c 'guardian_agent &'")

        # Create session entry with end_time and duration=0
        async with self.session_lock:
            start_time = self.active_sessions[session_id]["start_time"]
        self.storage.add_session(
            session_id,
            username,
            uid,
            start_time,
            0.0,
            0.0,
            desktop,
            service,
        )
        logger.info(f"Login: {username} (UID {uid}) Session {session_id}")

    async def handle_logout(self, session_id):
        """
        End a session on logout and save it in the database for child accounts.

        Args:
            session_id (str): Session ID
        """
        async with self.session_lock:
            session = self.active_sessions.pop(session_id, None)
            lock_periods = self.session_locks.pop(session_id, [])
        if session:
            kids = set(self.policy.data.get("users", {}).keys())
            if session["username"] not in kids:
                logger.info(
                    f"Ignoring logout from {session['username']} Session {session_id}"
                )
                return
            end_time = time.time()  # Store as epoch
            duration = end_time - session["start_time"]
            # Deduct locked time
            locked_time = 0.0
            for lock_start, lock_end in lock_periods:
                if lock_end is not None:
                    locked_time += max(0.0, lock_end - lock_start)
                else:
                    locked_time += max(0.0, end_time - lock_start)
            effective_duration = duration - locked_time
            if effective_duration < 0:
                logger.warning(
                    f"Negative session duration detected for {session_id}, setting to 0."
                )
                effective_duration = 0.0
            logger.debug(
                f"Session {session_id}: raw duration={duration:.1f}s, locked={locked_time:.1f}s, effective={effective_duration:.1f}s"
            )
            # Update session entry
            self.storage.update_session_logout(session_id, end_time, effective_duration)
            logger.info(
                f"Logout: {session['username']} Session {session_id} Duration: {effective_duration:.1f}s (locked: {locked_time:.1f}s)"
            )

            # Check if this is the last session for this user today
            async with self.session_lock:
                user_has_active_sessions = any(
                    s["username"] == session["username"]
                    for s in self.active_sessions.values()
                )

            # If this was the last active session, consider summarizing the day
            if not user_has_active_sessions:
                # Check time of day - if it's late in the day (past 8 PM), summarize
                hour = datetime.datetime.now().hour
                if hour >= 20:  # 8 PM
                    await self.check_usage_summarize(
                        session["username"], effective_duration
                    )

    async def check_quota(self, username: str) -> bool:
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
            return True  # User is not monitored

        total_allowed = await self.get_total_time(username)
        used_time = await self._calculate_used_time(username)

        # If the quota is reached, trigger the summarization
        if used_time >= total_allowed:
            await self.check_usage_summarize(username, used_time, quota_reached=True)
            logger.info(
                f"Quota reached for {username}: {used_time/60:.1f} minutes used of {total_allowed/60:.1f} minute quota"
            )

        return used_time < total_allowed

    async def run(self):
        """
        Start session tracking, connect to systemd-logind via DBus, and listen for KDE lock events.
        Also checks for already logged-in child sessions on startup.
        Periodically updates session progress in the database.
        """
        # Check for daily reset on startup/wake
        await self.check_daily_reset_on_startup()

        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        await bus.request_name("org.guardian.Daemon")
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
                    logger.info(
                        f"Startup: found active child session {session_id} for {username}"
                    )
                    await self.handle_login(session_id, uid, username, props)
                except Exception as e:
                    logger.error(
                        f"Error processing existing session {session_id} for {username}: {e}"
                    )

        def session_new_handler(session_id, object_path):
            async def inner():
                session_obj = bus.get_proxy_object(
                    "org.freedesktop.login1",
                    object_path,
                    await bus.introspect("org.freedesktop.login1", object_path),
                )
                session_iface = session_obj.get_interface(
                    "org.freedesktop.login1.Session"
                )
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
                username = props.get("Name", None)
                user_struct = props.get("User", None)
                uid = (
                    user_struct[0]
                    if isinstance(user_struct, (list, tuple))
                    else user_struct
                )
                kids = set(self.policy.data.get("users", {}).keys())
                if username not in kids:
                    logger.debug(
                        f"Ignoring session from {username} (UID {uid}) Session {session_id}"
                    )
                    return
                logger.debug(
                    f"Extracted: Name={username}, UID={uid}, Desktop={props.get('Desktop')}, Service={props.get('Service')}"
                )
                await self.handle_login(session_id, uid, username, props)

            asyncio.create_task(inner())

        def session_removed_handler(session_id, object_path):
            asyncio.create_task(self.handle_logout(session_id))

        manager.on_session_new(session_new_handler)
        manager.on_session_removed(session_removed_handler)

        # Start periodic session update task
        asyncio.create_task(self.periodic_session_update(interval=60))

        logger.info("SessionTracker running. Waiting for logins/logouts...")
        while True:
            await asyncio.sleep(3600)

    async def _get_username(self, uid):
        """
        Get the username for a given UID.

        Args:
            uid (int): User ID

        Returns:
            str: Username
        """

        try:
            return await asyncio.to_thread(pwd.getpwuid, uid).pw_name
        except Exception:
            return str(uid)

    async def perform_daily_reset(self):
        """
        Perform daily reset: summarize sessions, create history entries,
        clean up sessions table, and reset quotas.

        This should be called when:
        1. The system is first booted/unlocked for the day
        2. The daily quota is reached
        3. The configured reset_time is reached
        """
        today = datetime.date.today().strftime("%Y-%m-%d")
        last_reset_date = self.storage.get_last_reset_date()

        # Check if we already reset today
        if last_reset_date == today:
            logger.debug(f"Daily reset already performed today ({today})")
            return

        logger.info(
            f"Performing daily reset. Last reset: {last_reset_date}, Today: {today}"
        )

        # Get all managed users
        kids = set(self.policy.data.get("users", {}).keys())

        # Process each child user
        for username in kids:
            try:
                # Get settings to determine quota
                user_settings = self.policy.get_user_policy(username)
                daily_quota_minutes = user_settings.get(
                    "daily_quota_minutes", 90
                )  # Default 90 minutes

                # Summarize sessions for this user
                summary = self.storage.summarize_user_sessions(
                    username, last_reset_date
                )

                # Check if quota was exceeded
                if summary["total_screen_time"] > (daily_quota_minutes * 60):
                    summary["quota_exceeded"] = True

                # TODO: Calculate bonus time used (future feature)

                # Save to history
                self.storage.save_history_entry(summary)

                # Clean up old sessions
                self.storage.clean_old_sessions(username, today)

                logger.info(
                    f"Reset completed for user {username}: {summary['total_screen_time']/60:.1f} minutes used"
                )
            except Exception as e:
                logger.error(f"Error during reset for user {username}: {e}")

        # Update last reset date
        self.storage.set_last_reset_date(today)
        logger.info(f"Daily reset complete. Updated last reset date to {today}")

    async def get_active_users(self) -> list:
        """
        Return a list of currently active usernames.
        """
        async with self.session_lock:
            return [session["username"] for session in self.active_sessions.values()]

    def pause_user_time(self, username, timestamp):
        """
        Pause time tracking for a user when a lock event is received for an unknown session.
        """
        # Implementation: mark user as locked, store timestamp
        if not hasattr(self, "user_locks"):
            self.user_locks = {}
        self.user_locks[username] = timestamp
        logger.info(f"User {username} time tracking paused at {timestamp}.")

    async def check_daily_reset_on_startup(self):
        """
        Check if we need to perform a daily reset on daemon startup or system wake.
        This is based on the date comparison between today and the last reset date.
        """
        today = datetime.date.today().strftime("%Y-%m-%d")
        last_reset_date = self.storage.get_last_reset_date()

        if today != last_reset_date:
            logger.info(
                f"First startup/wake of the day detected. Today: {today}, Last reset: {last_reset_date}"
            )

            # Check if there are any sessions from today
            c = self.storage.conn.cursor()
            today_start = datetime.datetime.combine(
                datetime.date.today(), datetime.time.min
            ).timestamp()

            c.execute(
                "SELECT COUNT(*) FROM sessions WHERE start_time >= ?", (today_start,)
            )
            today_sessions_count = c.fetchone()[0]

            if today_sessions_count == 0:
                logger.info("No sessions from today found. Performing daily reset.")
                await self.perform_daily_reset()
            else:
                logger.info(
                    f"Found {today_sessions_count} sessions from today. Skipping reset."
                )
        else:
            logger.debug(f"Reset already performed today ({today})")

    async def check_usage_summarize(self, username, used_time, quota_reached=False):
        """
        Check if we need to summarize usage and add to history.
        This should be called when:
        1. The user reaches their daily quota
        2. The user logs out and it's their last session of the day

        Args:
            username (str): Username to check
            used_time (float): Used time in seconds
            quota_reached (bool): Whether the quota was reached
        """
        # If quota is reached, immediately summarize and move to history
        if quota_reached:
            today = datetime.date.today().strftime("%Y-%m-%d")

            # Summarize sessions for this user
            summary = self.storage.summarize_user_sessions(username, today)
            summary["quota_exceeded"] = True

            # Save to history
            self.storage.save_history_entry(summary)

            logger.info(
                f"User {username} reached quota. Added summary to history: {summary['total_screen_time']/60:.1f} minutes used."
            )

            # We don't clean up sessions yet as the day isn't over
            # We'll do that during the next daily reset


class GuardianDaemonInterface(ServiceInterface):
    def __init__(self, session_tracker):
        super().__init__("org.guardian.Daemon")
        self.session_tracker = session_tracker

    @method()
    async def LockEvent(
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
        async with self.session_tracker.session_lock:
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
        await self.session_tracker.receive_lock_event(
            session_id, username, locked, timestamp
        )
        return None

    # pause_user_time is now implemented in SessionTracker


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
    user_manager = UserManager(policy)
    tracker = SessionTracker(policy, config, user_manager)
    asyncio.run(tracker.run())
# logind watcher
