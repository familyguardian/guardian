"""
Central SQLAlchemy interface for guardian-daemon.
Provides functions for session handling using SQLAlchemy ORM.
"""

import asyncio
import datetime
import json
import os
from datetime import datetime as dt
from datetime import timedelta
from typing import Optional

from sqlalchemy import and_, create_engine, delete, func, or_, select, text, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from guardian_daemon.logging import get_logger
from guardian_daemon.models import History, Meta, Session, UserSettings

logger = get_logger("Storage")


class Storage:
    """
    Central SQLAlchemy interface for session and settings storage in Guardian Daemon.

    Key design changes:
    - Uses SQLAlchemy ORM instead of raw SQL
    - Session.id is autoincrement (not using logind session_id as primary key)
    - logind_session_id is stored separately as it's transient
    - Date field tracks which day the session belongs to
    """

    @staticmethod
    def logind_to_epoch(logind_timestamp: int) -> float:
        """
        Convert logind timestamp (microseconds since boot) to EPOCH timestamp.

        Args:
            logind_timestamp (int): Microseconds since boot

        Returns:
            float: EPOCH timestamp
        """
        # Get system boot time in EPOCH seconds
        with open("/proc/stat") as f:
            for line in f:
                if line.startswith("btime"):
                    boot_time = int(line.split()[1])
                    break
        return boot_time + (logind_timestamp / 1_000_000)

    def __init__(self, db_path: str):
        """
        Initialize the Storage with the given database path.

        Args:
            db_path (str): Path to SQLite database.
        """
        self.db_path = db_path
        logger.info(f"Opening SQLite database at {self.db_path}")

        # Ensure parent directory exists
        parent_dir = os.path.dirname(self.db_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir)

        # Create SQLAlchemy engine with proper SQLite configuration
        # NullPool: creates a new connection for each thread (required for true concurrency)
        # StaticPool works for single-threaded access, but NullPool is safer for asyncio.to_thread()
        # This prevents "bad parameter or other API misuse" errors in concurrent scenarios
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            echo=False,
            poolclass=NullPool,
            connect_args={
                "check_same_thread": False,
                "timeout": 30,  # 30 second timeout for lock acquisition
            },
        )

        # Create session factory
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)

        # Initialize database schema
        self._init_db()

    def _init_db(self):
        """
        Initialize the SQLite database schema using Alembic migrations.
        Runs all pending migrations, sets SQLite pragmas, and initializes default values.
        """
        try:
            # Run Alembic migrations programmatically
            import os

            from alembic import command
            from alembic.config import Config

            # Get the alembic.ini path (relative to this file's directory)
            daemon_dir = os.path.dirname(os.path.dirname(__file__))
            alembic_ini_path = os.path.join(daemon_dir, "alembic.ini")

            # Create Alembic config
            alembic_cfg = Config(alembic_ini_path)

            # Set the DB_PATH environment variable so env.py picks it up
            # env.py uses get_url() which reads from DB_PATH environment variable
            old_db_path = os.environ.get("DB_PATH")
            os.environ["DB_PATH"] = self.db_path

            try:
                # Run migrations to latest revision
                command.upgrade(alembic_cfg, "head")
                logger.info("Database migrations completed successfully")
            finally:
                # Restore original DB_PATH
                if old_db_path is None:
                    os.environ.pop("DB_PATH", None)
                else:
                    os.environ["DB_PATH"] = old_db_path

            # Dispose and recreate engine to ensure migration changes are visible
            # This is important for SQLite to ensure all changes are flushed
            self.engine.dispose()
            self.engine = create_engine(
                f"sqlite:///{self.db_path}",
                echo=False,
                poolclass=NullPool,
                connect_args={
                    "check_same_thread": False,
                    "timeout": 30,
                },
            )
            self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)

            # Set SQLite pragmas
            with self.engine.connect() as conn:
                conn.execute(text("PRAGMA journal_mode=WAL"))
                conn.execute(text("PRAGMA foreign_keys=ON"))
                conn.commit()

            # Initialize default meta values
            with self.SessionLocal() as session:
                # Check if last_reset_date exists
                result = session.execute(
                    select(Meta).where(Meta.key == "last_reset_date")
                ).first()

                if not result:
                    meta = Meta(
                        key="last_reset_date", value=dt.today().strftime("%Y-%m-%d")
                    )
                    session.add(meta)
                    session.commit()

            logger.info("Database initialized successfully")
        except Exception as e:
            logger.error(f"DB error during database initialization: {e}")
            raise

    def sync_config_to_db(self, config: dict):
        """
        Synchronize configuration data to the database.
        Merges user settings with defaults to ensure complete configuration.

        Args:
            config (dict): Configuration data
        """
        logger.info("Synchronizing config to database")

        with self.SessionLocal() as session:
            # Check if default settings exist
            result = session.execute(
                select(UserSettings).where(UserSettings.username == "default")
            ).first()

            defaults = config.get("defaults", {})
            if not result and defaults:
                default_settings = UserSettings(
                    username="default", settings=json.dumps(defaults)
                )
                session.add(default_settings)

            # Add/update user settings, merging with defaults
            for username, user_config in config.get("users", {}).items():
                # Merge user settings with defaults
                # Start with defaults, then override with user-specific settings
                merged_settings = defaults.copy()

                # Deep merge for nested dicts (like curfew)
                for key, value in user_config.items():
                    if (
                        isinstance(value, dict)
                        and key in merged_settings
                        and isinstance(merged_settings[key], dict)
                    ):
                        # Deep merge nested dicts
                        merged_settings[key] = {**merged_settings[key], **value}
                    else:
                        # Override with user value
                        merged_settings[key] = value

                result = session.execute(
                    select(UserSettings).where(UserSettings.username == username)
                ).first()

                if result:
                    # Update existing
                    session.execute(
                        update(UserSettings)
                        .where(UserSettings.username == username)
                        .values(settings=json.dumps(merged_settings))
                    )
                else:
                    # Create new
                    user_settings = UserSettings(
                        username=username, settings=json.dumps(merged_settings)
                    )
                    session.add(user_settings)

            session.commit()

    def get_user_settings(self, username: str) -> Optional[dict]:
        """
        Retrieve user settings from the database for the given username.

        Args:
            username (str): Username

        Returns:
            dict | None: User settings or None
        """
        logger.debug(f"Fetching settings for user: {username}")

        with self.SessionLocal() as session:
            result = session.execute(
                select(UserSettings).where(UserSettings.username == username)
            ).scalar_one_or_none()

            if result:
                return json.loads(result.settings)

            logger.debug(f"No settings found for user: {username}")
            return None

    def set_user_settings(self, username: str, settings: dict):
        """
        Store user settings in the database for the given username.

        Args:
            username (str): Username
            settings (dict): Settings dictionary
        """
        logger.info(f"Storing settings for user: {username}")

        with self.SessionLocal() as session:
            result = session.execute(
                select(UserSettings).where(UserSettings.username == username)
            ).scalar_one_or_none()

            if result:
                result.settings = json.dumps(settings)
            else:
                user_settings = UserSettings(
                    username=username, settings=json.dumps(settings)
                )
                session.add(user_settings)

            session.commit()

    async def add_session(
        self,
        session_id: str,
        username: str,
        uid: int,
        start_time: float,
        end_time: float,
        duration_seconds: float,
        desktop: Optional[str] = None,
        service: Optional[str] = None,
    ):
        """
        Adds a new session to the database.

        Args:
            session_id (str): Logind session ID (transient identifier)
            username (str): Username
            uid (int): User ID
            start_time (float): Start time (EPOCH)
            end_time (float): End time (EPOCH, 0 if still active)
            duration_seconds (float): Session duration in seconds
            desktop (str, optional): Desktop environment
            service (str, optional): Service (e.g. sddm)
        """
        # Convert logind timestamps if needed
        if isinstance(start_time, int) and start_time > 1e12:
            start_time = self.logind_to_epoch(start_time)
        if isinstance(end_time, int) and end_time > 1e12:
            end_time = self.logind_to_epoch(end_time)

        # Determine the date for this session
        session_date = dt.fromtimestamp(start_time).date()

        logger.info(
            f"Adding new session for user: {username}, logind_session_id: {session_id}, date: {session_date}"
        )

        def _add():
            with self.SessionLocal() as db_session:
                new_session = Session(
                    logind_session_id=session_id,
                    username=username,
                    uid=uid,
                    date=session_date,
                    start_time=start_time,
                    end_time=end_time if end_time != 0 else None,
                    duration=duration_seconds,
                    desktop=desktop,
                    service=service,
                )
                db_session.add(new_session)
                db_session.commit()

        await asyncio.to_thread(_add)

    def update_session_progress(self, session_id: str, duration_seconds: float):
        """
        Periodically update session entry with current duration (while session is active).
        This is critical for preserving session time across daemon restarts.

        Args:
            session_id (str): The logind session ID to update
            duration_seconds (float): Duration in seconds
        """
        with self.SessionLocal() as session:
            # Find active session with this logind_session_id
            result = session.execute(
                select(Session).where(
                    and_(
                        Session.logind_session_id == session_id,
                        or_(Session.end_time == None, Session.end_time == 0),
                    )
                )
            ).scalar_one_or_none()

            if not result:
                logger.warning(
                    f"Cannot update non-existent or closed session: {session_id}"
                )
                return

            # Update duration only if it's larger (prevent race conditions)
            if result.duration is None or duration_seconds > result.duration:
                result.duration = duration_seconds
                session.commit()

                logger.debug(
                    f"Updated session progress for logind_session_id: {session_id}, "
                    f"duration: {duration_seconds / 60:.1f} min"
                )

    def update_session_logout(
        self, session_id: str, end_time: float, duration_seconds: float
    ):
        """
        Update session entry with logout time and duration.

        Args:
            session_id (str): Logind session ID to update
            end_time (float): End time in EPOCH seconds
            duration_seconds (float): Session duration in seconds
        """
        logger.info(f"Updating session logout for logind_session_id: {session_id}")

        with self.SessionLocal() as session:
            session.execute(
                update(Session)
                .where(
                    and_(
                        Session.logind_session_id == session_id,
                        or_(Session.end_time == None, Session.end_time == 0),
                    )
                )
                .values(end_time=end_time, duration=duration_seconds)
            )
            session.commit()

    async def add_session_time(
        self, username: str, start_time: datetime, end_time: datetime
    ):
        """Add a usage time entry for a user.

        Args:
            username (str): Username
            start_time (datetime): Start time
            end_time (datetime): End time
        """
        duration_seconds = (end_time - start_time).total_seconds()
        await self.add_session(
            f"usage_{int(start_time.timestamp())}",
            username,
            1000,
            start_time.timestamp(),
            end_time.timestamp(),
            duration_seconds,
        )

    async def get_active_session(self, username: str, session_id: str):
        """Get an active session for a user.

        Args:
            username (str): Username
            session_id (str): Logind session ID

        Returns:
            tuple: Session data or None if not found
        """

        def _get():
            with self.SessionLocal() as session:
                result = session.execute(
                    select(Session).where(
                        and_(
                            Session.username == username,
                            Session.logind_session_id == session_id,
                            or_(Session.end_time == None, Session.end_time == 0),
                        )
                    )
                ).scalar_one_or_none()

                if result:
                    return (
                        result.username,
                        result.logind_session_id,
                        dt.fromtimestamp(result.start_time).isoformat(),
                    )
                return None

        return await asyncio.to_thread(_get)

    async def get_daily_usage(self, username: str, date: dt.date):
        """Get total usage time for a user on a given date.

        Args:
            username (str): Username
            date (dt.date): Date to check

        Returns:
            int: Total usage time in seconds
        """

        def _get():
            with self.SessionLocal() as session:
                result = session.execute(
                    select(func.coalesce(func.sum(Session.duration), 0)).where(
                        and_(Session.username == username, Session.date == date)
                    )
                ).scalar()

                return int(result) if result else 0

        return await asyncio.to_thread(_get)

    async def end_session(self, username: str, session_id: str, end_time: datetime):
        """End a session for a user.

        Args:
            username (str): Username
            session_id (str): Logind session ID
            end_time (datetime): End time
        """

        def _end():
            with self.SessionLocal() as session:
                session.execute(
                    update(Session)
                    .where(
                        and_(
                            Session.username == username,
                            Session.logind_session_id == session_id,
                        )
                    )
                    .values(end_time=end_time.timestamp())
                )
                session.commit()

        await asyncio.to_thread(_end)

    async def get_weekly_usage(self, username: str, date: dt.date):
        """Get total usage time for a user in the week containing the given date.

        Args:
            username (str): Username
            date (dt.date): Date within the week to check

        Returns:
            int: Total usage time in seconds
        """

        def _get():
            # Calculate week boundaries
            week_start = date - timedelta(days=date.weekday())
            week_end = week_start + timedelta(days=7)

            with self.SessionLocal() as session:
                result = session.execute(
                    select(func.sum(Session.duration)).where(
                        and_(
                            Session.username == username,
                            Session.date >= week_start,
                            Session.date < week_end,
                        )
                    )
                ).scalar()

                return int(result) if result else 0

        return await asyncio.to_thread(_get)

    async def cleanup_stale_sessions(self, max_age_hours: int):
        """Remove sessions older than the specified age.

        Args:
            max_age_hours (int): Maximum age in hours to keep sessions
        """

        def _cleanup():
            cutoff_time = dt.now() - timedelta(hours=max_age_hours)
            cutoff_timestamp = cutoff_time.timestamp()

            with self.SessionLocal() as session:
                session.execute(
                    delete(Session).where(Session.start_time < cutoff_timestamp)
                )
                session.commit()

        await asyncio.to_thread(_cleanup)

    async def get_all_active_sessions(self):
        """Get all currently active sessions.

        Returns:
            list: List of active sessions
        """

        def _get():
            current_time = dt.now().timestamp()

            with self.SessionLocal() as session:
                results = (
                    session.execute(
                        select(Session).where(
                            or_(
                                Session.end_time > current_time,
                                Session.end_time == None,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )

                return [
                    (
                        r.username,
                        r.logind_session_id,
                        r.start_time,
                        r.end_time,
                        r.duration,
                    )
                    for r in results
                ]

        return await asyncio.to_thread(_get)

    async def get_usage_in_date_range(
        self, username: str, start_date: datetime, end_date: datetime
    ):
        """Get total usage time for a user between two dates.

        Args:
            username (str): Username
            start_date (datetime): Start date
            end_date (datetime): End date

        Returns:
            int: Total usage time in seconds
        """

        def _get():
            with self.SessionLocal() as session:
                result = session.execute(
                    select(func.sum(Session.duration)).where(
                        and_(
                            Session.username == username,
                            Session.start_time >= start_date.timestamp(),
                            Session.start_time < end_date.timestamp(),
                        )
                    )
                ).scalar()

                return int(result) if result else 0

        return await asyncio.to_thread(_get)

    def get_sessions_for_user(
        self, username: str, since: Optional[float] = None
    ) -> list:
        """
        Retrieve all sessions for a user, optionally since a specific time.

        Args:
            username (str): Username
            since (float, optional): Start time (Unix timestamp)

        Returns:
            list: List of sessions as tuples
        """
        logger.debug(f"Fetching sessions for user: {username}, since: {since}")

        with self.SessionLocal() as session:
            query = select(Session).where(Session.username == username)

            if since:
                query = query.where(Session.start_time >= since)

            results = session.execute(query).scalars().all()

            # Convert to tuple format for backwards compatibility
            sessions = [
                (
                    r.logind_session_id,
                    r.username,
                    r.uid,
                    r.start_time,
                    r.end_time if r.end_time else 0,
                    r.duration if r.duration else 0,
                    r.desktop,
                    r.service,
                )
                for r in results
            ]

            logger.debug(f"Found {len(sessions)} sessions for user: {username}")
            return sessions

    def get_all_usernames(self) -> list:
        """
        Return all usernames (except 'default') from the database.

        Returns:
            list: List of usernames
        """
        logger.debug("Fetching all usernames except 'default'")

        with self.SessionLocal() as session:
            results = (
                session.execute(
                    select(UserSettings.username).where(
                        UserSettings.username != "default"
                    )
                )
                .scalars()
                .all()
            )

            usernames = list(results)
            logger.debug(f"Found usernames: {usernames}")
            return usernames

    def get_open_sessions(self) -> list:
        """
        Get all currently open sessions from the database.

        Returns:
            list: List of tuples (logind_session_id, username, uid, start_time, duration, desktop, service)
        """
        with self.SessionLocal() as session:
            results = (
                session.execute(
                    select(Session).where(
                        or_(Session.end_time == None, Session.end_time == 0)
                    )
                )
                .scalars()
                .all()
            )

            return [
                (
                    r.logind_session_id,
                    r.username,
                    r.uid,
                    r.start_time,
                    r.duration if r.duration else 0,
                    r.desktop,
                    r.service,
                )
                for r in results
            ]

    def get_sessions_count_since(self, timestamp: float) -> int:
        """
        Get count of sessions since a given timestamp.

        Args:
            timestamp (float): Unix timestamp

        Returns:
            int: Count of sessions
        """
        with self.SessionLocal() as session:
            result = session.execute(
                select(func.count(Session.id)).where(Session.start_time >= timestamp)
            ).scalar()

            return result if result else 0

    def delete_sessions_since(self, since: float):
        """
        Delete all sessions from the database since the given timestamp.

        Args:
            since (float): Start timestamp (Unix timestamp)
        """
        logger.info(f"Deleting sessions since timestamp: {since}")

        with self.SessionLocal() as session:
            session.execute(delete(Session).where(Session.start_time >= since))
            session.commit()

    def get_last_reset_timestamp(self) -> Optional[float]:
        """
        Retrieve the last daily reset timestamp from the database.

        Returns:
            float | None: EPOCH timestamp of last reset or None
        """
        with self.SessionLocal() as session:
            result = session.execute(
                select(Meta).where(Meta.key == "last_reset")
            ).scalar_one_or_none()

            if result:
                try:
                    return float(result.value)
                except ValueError:
                    return None
            return None

    def set_last_reset_timestamp(self, ts: float):
        """
        Store the last daily reset timestamp in the database.

        Args:
            ts (float): EPOCH timestamp
        """
        with self.SessionLocal() as session:
            result = session.execute(
                select(Meta).where(Meta.key == "last_reset")
            ).scalar_one_or_none()

            if result:
                result.value = str(ts)
            else:
                meta = Meta(key="last_reset", value=str(ts))
                session.add(meta)

            session.commit()

    def get_last_reset_date(self) -> str:
        """
        Retrieve the last daily reset date from the database.

        Returns:
            str: Date in YYYY-MM-DD format
        """
        with self.SessionLocal() as session:
            result = session.execute(
                select(Meta).where(Meta.key == "last_reset_date")
            ).scalar_one_or_none()

            if result:
                return result.value

            # Default to today if not found
            return dt.today().strftime("%Y-%m-%d")

    def set_last_reset_date(self, date_str: str):
        """
        Store the last daily reset date in the database.

        Args:
            date_str (str): Date in YYYY-MM-DD format
        """
        with self.SessionLocal() as session:
            result = session.execute(
                select(Meta).where(Meta.key == "last_reset_date")
            ).scalar_one_or_none()

            if result:
                result.value = date_str
            else:
                meta = Meta(key="last_reset_date", value=date_str)
                session.add(meta)

            session.commit()

    def summarize_user_sessions(self, username: str, date: str = None):
        """
        Summarize all sessions for a user on a given date and create a history entry.
        If date is not provided, summarize sessions from the most recent day.

        Args:
            username (str): Username to summarize sessions for
            date (str, optional): Date in YYYY-MM-DD format, defaults to today

        Returns:
            dict: Summary of session data
        """
        if not date:
            date = dt.today().strftime("%Y-%m-%d")

        # Convert date string to date object
        date_obj = dt.strptime(date, "%Y-%m-%d").date()

        with self.SessionLocal() as session:
            # Query sessions for this user on this day
            results = (
                session.execute(
                    select(Session)
                    .where(and_(Session.username == username, Session.date == date_obj))
                    .order_by(Session.start_time)
                )
                .scalars()
                .all()
            )

            # Calculate summary statistics
            total_screen_time = 0
            login_count = len(results)
            first_login = None
            last_logout = None

            for sess in results:
                # Add duration if available
                if sess.duration:
                    total_screen_time += sess.duration

                # Track first login
                if first_login is None or sess.start_time < first_login:
                    first_login = sess.start_time

                # Track last logout
                if sess.end_time:
                    if last_logout is None or sess.end_time > last_logout:
                        last_logout = sess.end_time

            # Create summary object
            summary = {
                "username": username,
                "date": date,
                "total_screen_time": int(total_screen_time),
                "login_count": login_count,
                "first_login": (
                    dt.fromtimestamp(first_login).isoformat() if first_login else None
                ),
                "last_logout": (
                    dt.fromtimestamp(last_logout).isoformat()
                    if last_logout and last_logout != 0
                    else None
                ),
                "quota_exceeded": False,
                "bonus_time_used": 0,
                "created_at": dt.now().isoformat(),
            }

            return summary

    def save_history_entry(self, summary: dict):
        """
        Save a history entry from a session summary.

        Args:
            summary (dict): Session summary data
        """
        with self.SessionLocal() as session:
            # Check if entry already exists
            result = session.execute(
                select(History).where(
                    and_(
                        History.username == summary["username"],
                        History.date == summary["date"],
                    )
                )
            ).scalar_one_or_none()

            if result:
                # Update existing
                result.total_screen_time = summary["total_screen_time"]
                result.login_count = summary["login_count"]
                result.first_login = summary["first_login"]
                result.last_logout = summary["last_logout"]
                result.quota_exceeded = 1 if summary["quota_exceeded"] else 0
                result.bonus_time_used = summary["bonus_time_used"]
                result.created_at = summary["created_at"]
            else:
                # Create new
                history = History(
                    username=summary["username"],
                    date=summary["date"],
                    total_screen_time=summary["total_screen_time"],
                    login_count=summary["login_count"],
                    first_login=summary["first_login"],
                    last_logout=summary["last_logout"],
                    quota_exceeded=1 if summary["quota_exceeded"] else 0,
                    bonus_time_used=summary["bonus_time_used"],
                    created_at=summary["created_at"],
                )
                session.add(history)

            session.commit()
            logger.info(
                f"Saved history entry for {summary['username']} on {summary['date']}"
            )

    def clean_old_sessions(self, username: str, before_date: str = None):
        """
        Remove old session records for a user after they've been summarized to history.

        Args:
            username (str): Username to clean sessions for
            before_date (str, optional): Remove sessions before this date (YYYY-MM-DD)
                                         If not provided, removes all sessions
        """
        with self.SessionLocal() as session:
            if before_date:
                date_obj = dt.strptime(before_date, "%Y-%m-%d").date()
                session.execute(
                    delete(Session).where(
                        and_(Session.username == username, Session.date < date_obj)
                    )
                )
            else:
                session.execute(delete(Session).where(Session.username == username))

            session.commit()
            logger.info(f"Cleaned old sessions for {username}")

    def get_history(self, username: str, start_date: str = None, end_date: str = None):
        """
        Retrieve history entries for a user within a date range.

        Args:
            username (str): Username to get history for
            start_date (str, optional): Start date in YYYY-MM-DD format
            end_date (str, optional): End date in YYYY-MM-DD format

        Returns:
            list: List of history entries as dictionaries
        """
        with self.SessionLocal() as session:
            query = select(History).where(History.username == username)

            if start_date:
                query = query.where(History.date >= start_date)

            if end_date:
                query = query.where(History.date <= end_date)

            query = query.order_by(History.date.desc())

            results = session.execute(query).scalars().all()

            # Convert to dictionaries
            history_entries = []
            for r in results:
                history_entries.append(
                    {
                        "username": r.username,
                        "date": r.date,
                        "total_screen_time": r.total_screen_time,
                        "login_count": r.login_count,
                        "first_login": r.first_login,
                        "last_logout": r.last_logout,
                        "quota_exceeded": bool(r.quota_exceeded),
                        "bonus_time_used": r.bonus_time_used,
                        "created_at": r.created_at,
                    }
                )

            return history_entries

    def close(self):
        """
        Close the database connection.
        """
        logger.info("Closing SQLite database connection")
        self.engine.dispose()
