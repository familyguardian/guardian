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
        Stores the mapping in self.agent_name_map: {username: {dbus_name, ...}}
        """
        self.agent_name_map = {}
        kids = set(self.policy.data.get("users", {}).keys())
        for username in kids:
            try:
                await self.discover_agent_names_for_user(username)
                # Get the names after discovery
                agent_names = self.agent_name_map.get(username, [])
                logger.debug(
                    f"Refreshed agent D-Bus names for {username}: {agent_names}"
                )
            except Exception as e:
                logger.error(f"Error refreshing agent names for {username}: {e}")

    def get_agent_names_for_user(self, username: str) -> list:
        """
        Return the cached list of D-Bus agent names for a user, or empty list if not found.
        Converts from set to list if necessary.
        """
        agent_names = self.agent_name_map.get(username, [])
        # Convert to list if it's a set
        if isinstance(agent_names, set):
            return list(agent_names)
        return agent_names

    async def discover_agent_names_for_user(self, username: str):
        """
        Scans the D-Bus for all available agent service names for a given user
        and updates the internal map.
        """
        try:
            bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            introspection = await bus.introspect(
                "org.freedesktop.DBus", "/org/freedesktop/DBus"
            )
            obj = bus.get_proxy_object(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", introspection
            )
            dbus_iface = obj.get_interface("org.freedesktop.DBus")

            all_names = await dbus_iface.call_list_names()
            prefix = f"org.guardian.Agent.{username}."

            user_agents = {name for name in all_names if name.startswith(prefix)}

            # Update the map, ensuring we only add active names
            self.agent_name_map[username] = user_agents

            if user_agents:
                logger.info(
                    f"Initial scan found active agents for {username}: {user_agents}"
                )
            else:
                logger.info(f"Initial scan found no active agents for {username}")

        except Exception as e:
            logger.error(f"Failed to discover agent names for {username}: {e}")

    def _handle_name_owner_changed(self, name: str, old_owner: str, new_owner: str):
        """Signal handler for D-Bus name ownership changes."""
        try:
            if name.startswith("org.guardian.Agent."):
                parts = name.split(".")
                if len(parts) >= 5:
                    username = parts[3]
                    if new_owner and not old_owner:
                        logger.info(f"Agent for {username} appeared: {name}")
                        if username not in self.agent_name_map:
                            self.agent_name_map[username] = set()
                        self.agent_name_map[username].add(name)
                    elif old_owner and not new_owner:
                        logger.info(f"Agent for {username} disappeared: {name}")
                        if username in self.agent_name_map:
                            self.agent_name_map[username].discard(name)
                            if not self.agent_name_map[username]:
                                del self.agent_name_map[username]
                else:
                    logger.warning(f"Received malformed agent bus name: {name}")
        except Exception as e:
            logger.error(f"Error handling name owner change for {name}: {e}")

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

    async def get_total_time(self, username: str) -> float:
        """
        Returns the total allowed time (in minutes) for the given user today.

        Returns:
            float: Total allowed screen time in minutes
        """
        user_policy = self.policy.get_user_policy(username)
        if user_policy is None:
            return float("inf")  # Unlimited if not monitored
        daily_quota, _ = self.policy.get_user_quota(username)
        return float(daily_quota)

    async def get_remaining_time(self, username: str) -> float:
        """
        Returns the remaining allowed time for the given user today.
        All calculations use minutes as the base unit to match the API contract.

        Returns:
            float: Remaining screen time in minutes
        """
        total_allowed = await self.get_total_time(username)  # In minutes
        if total_allowed == float("inf"):
            logger.debug(f"User {username} has unlimited time")
            return float("inf")

        # Simplified used time calculation
        async with self.session_lock:
            active_duration_seconds = 0
            now = time.time()
            for session in self.active_sessions.values():
                if (
                    session["username"] == username
                    and session.get("desktop")
                    and session.get("service") != "systemd-user"
                ):
                    active_duration_seconds += now - session["start_time"]

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

        active_session_ids = set(self.active_sessions.keys())
        filtered_sessions = [
            s
            for s in db_sessions
            if s[6] > 30  # Has meaningful duration
            and s[8] != "systemd-user"  # Not a systemd-user session
            and s[7]  # Has a desktop value
            and s[0] not in active_session_ids  # Not currently active
        ]
        db_duration_seconds = sum(s[6] for s in filtered_sessions)

        total_seconds = active_duration_seconds + db_duration_seconds
        used_minutes = total_seconds / 60

        remaining = total_allowed - used_minutes
        logger.debug(
            f"User {username} quota: total={total_allowed}, used={used_minutes:.1f}, remaining={remaining:.1f} minutes"
        )
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
                        # Mark lock end and adjust session start to exclude locked period
                        self.session_locks[session_id][i] = (lock_start, timestamp)
                        session = self.active_sessions.get(session_id)
                        if session:
                            locked_duration = timestamp - lock_start
                            session["start_time"] += locked_duration
                        logger.debug(
                            f"Session {session_id}: screen unlocked at {timestamp}, excluded {locked_duration:.1f}s locked"
                        )
                        # Remove the processed lock entry
                        self.session_locks[session_id].pop(i)
                        break
            # No longer updating DB here; periodic task handles it.

    async def periodic_session_update(self, interval: int = 60):
        """
        Periodically update all active sessions in the database with current duration.
        This is critical for preserving session time across daemon restarts.
        """
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        logind = bus.get_proxy_object(
            "org.freedesktop.login1", "/org/freedesktop/login1", await bus.introspect("org.freedesktop.login1", "/org/freedesktop/login1")
        )
        manager = logind.get_interface("org.freedesktop.login1.Manager")

        while True:
            try:
                now = time.time()
                active_session_ids = list(self.active_sessions.keys())

                for session_id in active_session_ids:
                    session = self.active_sessions.get(session_id)
                    if not session:
                        continue

                    try:
                        session_path = await manager.call_get_session(session_id)
                        session_obj = bus.get_proxy_object(
                            "org.freedesktop.login1",
                            session_path,
                            await bus.introspect("org.freedesktop.login1", session_path),
                        )
                        session_iface = session_obj.get_interface(
                            "org.freedesktop.login1.Session"
                        )
                        is_locked = await session_iface.get_locked_hint()

                        # If session is locked, we should not accumulate time
                        if is_locked:
                            logger.debug(
                                f"Session {session_id} for user {session['username']} is locked, pausing time accumulation."
                            )
                            # By continuing, we skip the duration update for this locked session
                            continue

                    except Exception as e:
                        logger.error(
                            f"Failed to check lock state for session {session_id}: {e}"
                        )

                    async with self.session_lock:
                        # Re-fetch session data in case it changed
                        session = self.active_sessions.get(session_id)
                        if not session:
                            continue

                        # Calculate raw duration since session start
                        raw_duration = now - session["start_time"]
                        # Subtract any locked-period durations so locked time isn't counted/frozen
                        locked_periods = self.session_locks.get(session_id, [])
                        locked_seconds = sum(
                            (lock_end or now) - lock_start
                            for lock_start, lock_end in locked_periods
                        )
                        duration = max(0.0, raw_duration - locked_seconds)

                        # Log useful debugging information about the session
                        username = session.get("username", "unknown")
                        logger.debug(
                            f"Updating session {session_id} for {username}: "
                            f"start_time={session['start_time']}, "
                            f"current_duration={duration/60:.1f} minutes"
                        )

                        # Ensure we always update the database with the latest duration
                        # This is crucial for preserving time across daemon restarts
                        self.storage.update_session_progress(session_id, duration)

                        # Log more detailed information at info level if significant time has passed
                        if duration > 300:  # More than 5 minutes
                            logger.info(
                                f"Session {session_id} for {username} has accumulated {duration/60:.1f} minutes"
                            )
            except Exception as e:
                logger.error(f"Error in periodic session update: {e}")

            # Wait for the next update interval
            await asyncio.sleep(interval)

    def __init__(self, policy: Policy, config: dict, user_manager: UserManager):
        """
        Initialize the SessionTracker with a policy, configuration, and user manager.

        Args:
            policy (Policy): Policy instance
            config (dict): Parsed configuration
            user_manager (UserManager): User manager instance for setting up user sessions
        """
        self.policy = policy
        if isinstance(config, Storage):
            self.storage = config
        else:
            db_path = config.get("db_path", "guardian.sqlite")
            self.storage = Storage(db_path)
        self.user_manager = user_manager
        self.active_sessions: dict[str, dict] = {}
        self.session_locks: dict[str, list[tuple[float, float | None]]] = {}
        self.session_lock = asyncio.Lock()
        self.agent_name_map: dict[str, set[str]] = {}

        # Restore active sessions from database (sessions with no end_time)
        self._restore_active_sessions()

    def _restore_active_sessions(self):
        """
        Restore sessions that are still open (no end_time) from the database into active_sessions.
        Properly preserves session duration across daemon restarts.
        """
        # First, clear any existing active sessions to avoid duplicates
        self.active_sessions = {}

        # Track unique sessions by session ID to avoid duplicates
        unique_sessions = {}

        # First, update all open sessions in the database with their current duration
        # This ensures we have the latest duration values before the daemon was stopped
        try:
            # Force an update of all active sessions in the database before proceeding
            # Get all open sessions from database
            c = self.storage.conn.cursor()
            c.execute(
                "SELECT session_id, username, start_time, duration FROM sessions WHERE end_time IS NULL OR end_time = 0"
            )
            open_sessions = c.fetchall()
            for session_row in open_sessions:
                session_id, username, start_time, duration = session_row
                logger.info(
                    f"Found open session {session_id} for {username} with duration {duration/60:.1f} min"
                )
        except Exception as e:
            logger.error(f"Error updating open sessions before restore: {e}")

        # Now get the latest data from the database
        c = self.storage.conn.cursor()
        c.execute(
            "SELECT session_id, username, uid, start_time, duration, desktop, service FROM sessions WHERE end_time IS NULL OR end_time = 0"
        )
        rows = c.fetchall()
        now = time.time()

        # First, collect all unique sessions by session_id
        for row in rows:
            # Safely handle both old and new DB schema versions by checking column count
            if len(row) >= 7:
                (
                    session_id,
                    username,
                    uid,
                    old_start_time,
                    duration,
                    desktop,
                    service,
                ) = row
            else:
                # Handle old schema that doesn't have desktop and service columns
                session_id, username, uid, old_start_time, duration = row
                desktop = ""
                service = ""

            # Only process each unique session_id once
            if session_id in unique_sessions:
                continue

            # Store this as a unique session
            unique_sessions[session_id] = {
                "uid": uid,
                "username": username,
                "old_start_time": old_start_time,
                "duration": duration
                or 0.0,  # Ensure we have a valid duration, default to 0
                "desktop": desktop,
                "service": service,
            }

        # Now process the unique sessions
        for session_id, session_data in unique_sessions.items():
            # Calculate adjusted start time to preserve accumulated session time
            adjusted_start_time = now - session_data["duration"]

            self.active_sessions[session_id] = {
                "uid": session_data["uid"],
                "username": session_data["username"],
                "start_time": adjusted_start_time,  # Key fix: adjusted start preserves previous usage
                "original_start_time": session_data["old_start_time"],
                "desktop": session_data["desktop"],
                "service": session_data["service"],
            }

            # For better tracking, record the preserved duration
            self.active_sessions[session_id]["preserved_duration"] = session_data[
                "duration"
            ]

            logger.info(
                f"Restored session {session_id} for {session_data['username']}: preserved duration {session_data['duration']/60:.1f} min"
            )
            self.session_locks[session_id] = []

        if rows:
            logger.info(
                f"Restored {len(rows)} active sessions from database on restart, preserving quota usage."
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
        # Set up user account with time rules and services
        if not await asyncio.to_thread(self.user_manager.setup_user_login, username):
            logger.error(
                f"Failed to set up user {username} for login. Skipping session."
            )
            return

        # Perform all user setup steps with a single call to UserManager
        # This handles time rules, group membership, and systemd service setup
        logger.info(f"Setting up user {username} for login session {session_id}")

        async with self.session_lock:
            self.active_sessions[session_id] = {
                "uid": uid,
                "username": username,
                "start_time": time.time(),  # UNIX epoch
                "desktop": desktop,
                "service": service,
            }
            self.session_locks[session_id] = []

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
                    if s["session_id"] != session_id
                )
                if not user_has_active_sessions:
                    # This is the last session, trigger summarization and cleanup
                    await self.check_usage_summarize(
                        session["username"],
                        effective_duration / 60,  # Convert to minutes
                        quota_reached=False,
                    )

    async def check_quota(self, username: str) -> bool:
        """
        Check if a user is within their quota limits.

        Args:
            username (str): Username

        Returns:
            bool: True if within quota, False if exceeded
        """
        # Check if user has quotas configured
        if not self.policy.has_quota(username):
            return True

        # Get configured quotas
        daily_quota, weekly_quota = self.policy.get_user_quota(username)
        today = datetime.datetime.now().date()

        # Get current usage
        daily_usage = await self.storage.get_daily_usage(username, today)
        weekly_usage = await self.storage.get_weekly_usage(username, today)

        # Check if either quota is exceeded
        if (daily_quota and daily_usage >= daily_quota) or (
            weekly_quota and weekly_usage >= weekly_quota
        ):
            logger.info(
                f"Quota exceeded for {username}: {daily_usage}s/{daily_quota}s daily, {weekly_usage}s/{weekly_quota}s weekly"
            )
            return False

        return True

    async def check_curfew(self, username: str, current_time, is_weekend: bool) -> bool:
        """Check if a user is allowed to log in at the current time based on curfew settings.

        Args:
            username (str): Username
            current_time: Current time to check (datetime.time)
            is_weekend (bool): Whether it's a weekend day

        Returns:
            bool: True if allowed, False if curfew is in effect
        """
        # If no curfew settings, always allow
        if not self.policy.has_curfew(username):
            return True

        # Get curfew settings for the given day type
        curfew = self.policy.get_user_curfew(username, is_weekend)
        if not curfew:
            return True  # No curfew for this day type means allowed

        # Parse curfew times
        start_time = datetime.datetime.strptime(curfew["start"], "%H:%M").time()
        end_time = datetime.datetime.strptime(curfew["end"], "%H:%M").time()

        # Check if current time is within allowed hours
        if start_time <= current_time <= end_time:
            return True

        logger.info(
            f"Curfew in effect for {username}: current {current_time}, allowed {start_time}-{end_time}"
        )
        return False

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

        # Set up D-Bus signal handler for agent appearance/disappearance
        dbus_introspection = await bus.introspect(
            "org.freedesktop.DBus", "/org/freedesktop/DBus"
        )
        dbus_obj = bus.get_proxy_object(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", dbus_introspection
        )
        dbus_iface = dbus_obj.get_interface("org.freedesktop.DBus")
        dbus_iface.on_name_owner_changed(self._handle_name_owner_changed)
        logger.info("Registered for D-Bus NameOwnerChanged signals.")

        # Initial scan for already running agents
        self.agent_name_map.clear()
        kids = set(self.policy.data.get("users", {}).keys())
        for username in kids:
            await self.discover_agent_names_for_user(username)

        # Check for already logged-in child sessions on startup
        sessions = await manager.call_list_sessions()
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
                try:
                    # Try to get session object - this might fail if the session is already gone
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

                    # Try to get the UID, with fallback to alternative methods
                    uid = None
                    try:
                        # First try the direct get_uid method
                        uid = await session_iface.get_uid()
                    except AttributeError:
                        # If get_uid fails, try get_user which might return a UID
                        try:
                            logger.debug("get_uid failed, trying get_user for session")
                            user = await session_iface.get_user()
                            if isinstance(user, int):
                                uid = user
                        except Exception:
                            # As a last resort, try to extract from properties
                            logger.debug("get_user failed, checking properties for UID")
                            if "User" in props and isinstance(props["User"], int):
                                uid = props["User"]

                    # If we still couldn't get the UID, we can't proceed
                    if uid is None:
                        logger.warning(
                            f"Failed to get UID for session {session_id}, cannot handle login"
                        )
                        return

                    username = await self._get_username(uid)
                    await self.handle_login(session_id, uid, username, props)
                except Exception as e:
                    logger.error(f"Error processing new session {session_id}: {e}")

            asyncio.create_task(inner())

        def session_removed_handler(session_id, object_path):
            async def safe_logout():
                try:
                    await self.handle_logout(session_id)
                except Exception as e:
                    logger.error(f"Error handling logout for session {session_id}: {e}")

            asyncio.create_task(safe_logout())

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

    async def perform_daily_reset(self, force=False):
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

        # Check if we already reset today (unless forced)
        if last_reset_date == today and not force:
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

    # The D-Bus signature is defined using string annotations, which is required by dbus-next.
    # Pylance reports these as undefined variables, so we suppress the warning.
    @method()
    async def LockEvent(
        self, session_id: "s", username: "s", locked: "b", timestamp: "d"  # pyright: ignore[reportUndefinedVariable] # noqa: F821
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
