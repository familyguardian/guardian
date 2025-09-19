"""
Central SQLite interface for guardian-daemon.
Provides functions for session handling and future extensions.
"""

import json
import sqlite3
from typing import Optional

from guardian_daemon.logging import get_logger

logger = get_logger("Storage")


class Storage:
    """
    Central SQLite interface for session and settings storage in Guardian Daemon.
    """

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

    def update_session_logout(self, session_id: str, end_time: float, duration: float):
        """
        Update session entry with logout time and duration.
        """
        c = self.conn.cursor()
        logger.info(f"Updating session logout for session_id: {session_id}")
        c.execute(
            """
            UPDATE sessions SET end_time = ?, duration = ? WHERE session_id = ? AND (end_time = 0 OR end_time IS NULL)
        """,
            (end_time, duration, session_id),
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
        self.conn = sqlite3.connect(self.db_path)
        self._init_db()

    def _init_db(self):
        """
        Initialize the SQLite database schema if not present.
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
                        duration REAL,
                        desktop TEXT,
                        service TEXT
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
        duration: float,
        desktop: Optional[str] = None,
        service: Optional[str] = None,
    ):
        """
        Adds a new session to the database.

        Args:
            session_id (str): Session ID
            username (str): Username
            uid (int): User ID
            start_time (float): Start time
            end_time (float): End time
            duration (float): Session duration
            desktop (str, optional): Desktop environment
            service (str, optional): Service (e.g. sddm)
        """
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
                duration,
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

    def close(self):
        """
        Close the database connection.
        """
        logger.info("Closing SQLite database connection")
        self.conn.close()


# SQLite storage
