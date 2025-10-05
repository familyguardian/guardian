"""
Central SQLite interface for guardian-daemon.
Provides functions for session handling and future extensions.
"""

import datetime
import json
import os
import sqlite3
import time
from typing import Optional

from guardian_daemon.logging import get_logger

logger = get_logger("Storage")


class Storage:
    """
    Central SQLite interface for session and settings storage in Guardian Daemon.
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
                    boot_time = int(line.strip().split()[1])
                    break
            else:
                raise RuntimeError(
                    "Could not determine system boot time from /proc/stat"
                )
        return boot_time + (logind_timestamp / 1_000_000)

    def update_session_progress(self, session_id: str, duration_seconds: float):
        """
        Periodically update session entry with current duration (while session is active).
        This is critical for preserving session time across daemon restarts.

        Args:
            session_id (str): The session ID to update
            duration_seconds (float): Duration in seconds
        """
        c = self.conn.cursor()

        # Verify the session exists and is still active
        c.execute(
            "SELECT session_id FROM sessions WHERE session_id = ? AND (end_time = 0 OR end_time IS NULL)",
            (session_id,),
        )
        if not c.fetchone():
            logger.warning(
                f"Cannot update non-existent or closed session: {session_id}"
            )
            return

        # Update the duration, ensuring we don't accidentally decrease it
        # This prevents any race conditions or timing issues from reducing tracked time
        c.execute(
            """
            UPDATE sessions
            SET duration = CASE
                WHEN ? > duration OR duration IS NULL THEN ?
                ELSE duration
            END
            WHERE session_id = ? AND (end_time = 0 OR end_time IS NULL)
            """,
            (duration_seconds, duration_seconds, session_id),
        )

        # Commit immediately to ensure data is persisted
        self.conn.commit()

        logger.debug(
            f"Updated session progress for session_id: {session_id}, duration: {duration_seconds/60:.1f} min (from {duration_seconds:.1f} sec)"
        )

    def get_user_settings(self, username: str) -> Optional[dict]:
        """
        Retrieve user settings from the database for the given username.

        Args:
            username (str): Nutzername

        Returns:
            dict | None: Einstellungen des Nutzers oder None
        """
        c = self.conn.cursor()
        logger.debug(f"Fetching settings for user: {username}")
        c.execute("SELECT settings FROM user_settings WHERE username=?", (username,))
        row = c.fetchone()
        if row:
            logger.debug(f"Settings found for user: {username}")
            return json.loads(row[0])
        logger.debug(f"No settings found for user: {username}")
        return None

    def set_user_settings(self, username: str, settings: dict):
        """
        Store user settings in the database for the given username.

        Args:
            username (str): Nutzername
            settings (dict): Einstellungen
        """
        c = self.conn.cursor()
        logger.info(f"Storing settings for user: {username}")
        c.execute(
            "INSERT OR REPLACE INTO user_settings (username, settings) VALUES (?, ?)",
            (username, json.dumps(settings)),
        )
        self.conn.commit()

    def update_session_logout(
        self, session_id: str, end_time: float, duration_seconds: float
    ):
        """
        Update session entry with logout time and duration.

        Args:
            session_id (str): Session ID to update
            end_time (float): End time in EPOCH seconds
            duration_seconds (float): Session duration in seconds
        """
        c = self.conn.cursor()
        logger.info(f"Updating session logout for session_id: {session_id}")
        c.execute(
            """
            UPDATE sessions SET end_time = ?, duration = ? WHERE session_id = ? AND (end_time = 0 OR end_time IS NULL)
        """,
            (end_time, duration_seconds, session_id),
        )
        self.conn.commit()

    def __init__(self, db_path: str):
        """
        Initialize the Storage with the given database path.

        Args:
            db_path (str): Pfad zur SQLite-Datenbank.
        """
        self.db_path = db_path
        logger.info(f"Opening SQLite database at {self.db_path}")
        # Ensure parent directory exists
        parent_dir = os.path.dirname(self.db_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self._init_db()

    def _init_db(self):
        """
        Initialize the SQLite database schema if not present.
        Also migrates schema to add missing columns.
        """
        try:
            with self.conn:
                logger.debug("Setting PRAGMA journal_mode=WAL and foreign_keys=ON")
                self.conn.execute("PRAGMA journal_mode=WAL;")
                self.conn.execute("PRAGMA foreign_keys=ON;")
                logger.debug("Ensuring sessions and user_settings tables exist")
                self.conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT,
                        username TEXT,
                        uid INTEGER,
                        start_time REAL,
                        end_time REAL,
                        duration REAL
                    )
                """
                )
                self.conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_settings (
                        username TEXT PRIMARY KEY,
                        settings TEXT
                    )
                """
                )
                c = self.conn.cursor()
                c.execute(
                    """
                    CREATE TABLE IF NOT EXISTS meta (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """
                )

                # Create history table for daily usage summaries
                self.conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT NOT NULL,
                        date TEXT NOT NULL,  -- Store as YYYY-MM-DD
                        total_screen_time INTEGER NOT NULL,  -- In seconds
                        login_count INTEGER NOT NULL,
                        first_login TEXT,  -- Timestamp
                        last_logout TEXT,  -- Timestamp
                        quota_exceeded BOOLEAN,
                        bonus_time_used INTEGER,  -- In seconds
                        created_at TEXT NOT NULL,  -- Timestamp when record was created
                        UNIQUE(username, date)
                    )
                """
                )

                # Add last_reset_date field to meta if it doesn't exist
                c.execute("SELECT value FROM meta WHERE key='last_reset_date'")
                if not c.fetchone():
                    self.conn.execute(
                        """
                        INSERT OR IGNORE INTO meta (key, value)
                        VALUES ('last_reset_date', date('now'))
                        """
                    )

                # Migrate sessions table to add missing columns
                c.execute("PRAGMA table_info(sessions)")
                columns = [row[1] for row in c.fetchall()]
                if "desktop" not in columns:
                    logger.info(
                        "Migrating DB: Adding 'desktop' column to sessions table."
                    )
                    self.conn.execute("ALTER TABLE sessions ADD COLUMN desktop TEXT")
                if "service" not in columns:
                    logger.info(
                        "Migrating DB: Adding 'service' column to sessions table."
                    )
                    self.conn.execute("ALTER TABLE sessions ADD COLUMN service TEXT")
        except Exception as e:
            logger.error(f"DB error during database initialization: {e}")

    def sync_config_to_db(self, config: dict):
        """
        Synchronize configuration data to the database.

        Args:
            config (dict): Konfigurationsdaten
        """
        # Defaults abgleichen
        logger.info("Synchronizing config to database")
        if self.get_user_settings("default") is None:
            defaults = config.get("defaults", {})
            logger.debug("Saving default settings to database")
            self.set_user_settings("default", defaults)
        for username, settings in config.get("users", {}).items():
            if self.get_user_settings(username) is None:
                if not settings:
                    settings = config.get("defaults", {})
                logger.debug(f"Saving settings for new user: {username}")
                self.set_user_settings(username, settings)

    def add_session(
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
            session_id (str): Session ID
            username (str): Username
            uid (int): User ID
            start_time (float): Start time (EPOCH)
            end_time (float): End time (EPOCH)
            duration_seconds (float): Session duration in seconds
            desktop (str, optional): Desktop environment
            service (str, optional): Service (e.g. sddm)
        """
        # If start_time or end_time are logind timestamps, convert them
        if isinstance(start_time, int) and start_time > 1e12:
            start_time = self.logind_to_epoch(start_time)
        if isinstance(end_time, int) and end_time > 1e12:
            end_time = self.logind_to_epoch(end_time)
        c = self.conn.cursor()
        logger.info(
            f"Adding new session for user: {username}, session_id: {session_id}"
        )
        c.execute(
            """
            INSERT INTO sessions (session_id, username, uid, start_time, end_time, duration, desktop, service)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                session_id,
                username,
                uid,
                start_time,
                end_time,
                duration_seconds,
                desktop,
                service,
            ),
        )
        self.conn.commit()

    def get_sessions_for_user(
        self, username: str, since: Optional[float] = None
    ) -> list:
        """
        Retrieve all sessions for a user, optionally since a specific time.

        Args:
            username (str): Username
            since (float, optional): Start time (Unix timestamp)

        Returns:
            list: List of sessions
        """
        c = self.conn.cursor()
        logger.debug(f"Fetching sessions for user: {username}, since: {since}")
        if since:
            c.execute(
                "SELECT * FROM sessions WHERE username=? AND start_time>=?",
                (username, since),
            )
        else:
            c.execute("SELECT * FROM sessions WHERE username=?", (username,))
        sessions = c.fetchall()
        logger.debug(f"Found {len(sessions)} sessions for user: {username}")
        return sessions

    def get_all_usernames(self) -> list:
        """
        Return all usernames (except 'default') from the database.

        Returns:
            list: List of usernames
        """
        c = self.conn.cursor()
        logger.debug("Fetching all usernames except 'default'")
        c.execute("SELECT username FROM user_settings WHERE username != 'default'")
        usernames = [row[0] for row in c.fetchall()]
        logger.debug(f"Found usernames: {usernames}")
        return usernames

    def delete_sessions_since(self, since: float):
        """
        Delete all sessions from the database since the given timestamp.

        Args:
            since (float): Startzeitpunkt (Unix-Timestamp)
        """
        c = self.conn.cursor()
        logger.info(f"Deleting sessions since timestamp: {since}")
        c.execute("DELETE FROM sessions WHERE start_time >= ?", (since,))
        self.conn.commit()

    def get_last_reset_timestamp(self) -> Optional[float]:
        """
        Retrieve the last daily reset timestamp from the database.
        Returns:
            float | None: EPOCH timestamp of last reset or None
        """
        c = self.conn.cursor()
        c.execute("SELECT value FROM meta WHERE key='last_reset'")
        row = c.fetchone()
        if row:
            try:
                return float(row[0])
            except Exception:
                return None
        return None

    def set_last_reset_timestamp(self, ts: float):
        """
        Store the last daily reset timestamp in the database.
        Args:
            ts (float): EPOCH timestamp
        """
        c = self.conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            ("last_reset", str(ts)),
        )
        self.conn.commit()

    def get_last_reset_date(self) -> str:
        """
        Retrieve the last daily reset date from the database.
        Returns:
            str: Date in YYYY-MM-DD format
        """
        c = self.conn.cursor()
        c.execute("SELECT value FROM meta WHERE key='last_reset_date'")
        row = c.fetchone()
        if row:
            return row[0]
        # Default to today if not found
        return datetime.date.today().strftime("%Y-%m-%d")

    def set_last_reset_date(self, date_str: str):
        """
        Store the last daily reset date in the database.
        Args:
            date_str (str): Date in YYYY-MM-DD format
        """
        c = self.conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            ("last_reset_date", date_str),
        )
        self.conn.commit()

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
        import datetime

        if not date:
            # Get most recent session date for user
            c = self.conn.cursor()
            c.execute(
                """
                SELECT date(start_time, 'unixepoch', 'localtime') as session_date
                FROM sessions
                WHERE username = ?
                ORDER BY start_time DESC LIMIT 1
                """,
                (username,),
            )
            result = c.fetchone()
            if result:
                date = result[0]
            else:
                # No sessions found, use today's date
                date = datetime.date.today().strftime("%Y-%m-%d")

        # Get start/end of day in local time
        date_obj = datetime.datetime.strptime(date, "%Y-%m-%d").date()
        start_of_day = datetime.datetime.combine(date_obj, datetime.time.min)
        end_of_day = datetime.datetime.combine(date_obj, datetime.time.max)

        # Convert to epoch timestamps
        start_ts = start_of_day.timestamp()
        end_ts = end_of_day.timestamp()

        # Query sessions for this user on this day
        c = self.conn.cursor()
        c.execute(
            """
            SELECT
                start_time,
                end_time,
                duration,
                session_id
            FROM sessions
            WHERE
                username = ? AND
                (
                    (start_time >= ? AND start_time <= ?) OR
                    (end_time >= ? AND end_time <= ?) OR
                    (start_time < ? AND (end_time > ? OR end_time = 0))
                )
            ORDER BY start_time
            """,
            (username, start_ts, end_ts, start_ts, end_ts, start_ts, start_ts),
        )
        sessions = c.fetchall()

        # Calculate summary statistics
        total_screen_time = 0
        login_count = len(sessions)
        first_login = None
        last_logout = None

        for start_time, end_time, session_id in sessions:
            # For sessions still in progress, use current time as end
            if not end_time or end_time == 0:
                end_time = time.time()

            # Only count time that falls within this day
            adjusted_start = max(start_time, start_ts)
            adjusted_end = min(end_time, end_ts)

            # Add the duration that falls within this day
            if adjusted_end > adjusted_start:
                total_screen_time += adjusted_end - adjusted_start

            # Track first login and last logout within this day
            if not first_login or start_time < first_login:
                first_login = start_time

            if not last_logout or (end_time and end_time > last_logout):
                last_logout = end_time

        # Create summary object
        summary = {
            "username": username,
            "date": date,
            "total_screen_time": int(total_screen_time),
            "login_count": login_count,
            "first_login": (
                datetime.datetime.fromtimestamp(first_login).isoformat()
                if first_login
                else None
            ),
            "last_logout": (
                datetime.datetime.fromtimestamp(last_logout).isoformat()
                if last_logout and last_logout != 0
                else None
            ),
            "quota_exceeded": False,  # Will be set by calling function if needed
            "bonus_time_used": 0,  # Will be set by calling function if needed
            "created_at": datetime.datetime.now().isoformat(),
        }

        return summary

    def save_history_entry(self, summary: dict):
        """
        Save a history entry from a session summary.

        Args:
            summary (dict): Session summary data
        """
        c = self.conn.cursor()
        c.execute(
            """
            INSERT OR REPLACE INTO history
            (username, date, total_screen_time, login_count, first_login, last_logout,
             quota_exceeded, bonus_time_used, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                summary["username"],
                summary["date"],
                summary["total_screen_time"],
                summary["login_count"],
                summary["first_login"],
                summary["last_logout"],
                1 if summary["quota_exceeded"] else 0,
                summary["bonus_time_used"],
                summary["created_at"],
            ),
        )
        self.conn.commit()
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
        c = self.conn.cursor()

        if before_date:
            # Convert date to timestamp
            date_obj = datetime.datetime.strptime(before_date, "%Y-%m-%d").date()
            cutoff_ts = datetime.datetime.combine(
                date_obj, datetime.time.min
            ).timestamp()

            # Get count before deletion
            c.execute(
                "SELECT COUNT(*) FROM sessions WHERE username = ? AND end_time < ?",
                (username, cutoff_ts),
            )
            count = c.fetchone()[0]

            # Delete sessions
            c.execute(
                "DELETE FROM sessions WHERE username = ? AND end_time < ?",
                (username, cutoff_ts),
            )
            self.conn.commit()
            logger.info(
                f"Removed {count} old sessions for {username} before {before_date}"
            )
        else:
            # Get count before deletion
            c.execute("SELECT COUNT(*) FROM sessions WHERE username = ?", (username,))
            count = c.fetchone()[0]

            # Delete all sessions for this user
            c.execute("DELETE FROM sessions WHERE username = ?", (username,))
            self.conn.commit()
            logger.info(f"Removed all {count} sessions for {username}")

    def get_history(self, username: str, start_date: str = None, end_date: str = None):
        """
        Retrieve history entries for a user within a date range.

        Args:
            username (str): Username to get history for
            start_date (str, optional): Start date in YYYY-MM-DD format
            end_date (str, optional): End date in YYYY-MM-DD format

        Returns:
            list: List of history entries
        """
        c = self.conn.cursor()
        params = [username]
        query = "SELECT * FROM history WHERE username = ?"

        if start_date:
            query += " AND date >= ?"
            params.append(start_date)

        if end_date:
            query += " AND date <= ?"
            params.append(end_date)

        query += " ORDER BY date DESC"

        c.execute(query, params)
        columns = [description[0] for description in c.description]
        history_entries = []

        for row in c.fetchall():
            history_entries.append(dict(zip(columns, row)))

        return history_entries

    def close(self):
        """
        Close the database connection.
        """
        logger.info("Closing SQLite database connection")
        self.conn.close()


# SQLite storage
