"""
Zentrale SQLite-Schnittstelle für guardian-daemon
Stellt Funktionen für Session-Handling und spätere Erweiterungen bereit.
"""

import json
import sqlite3
from typing import Optional


class Storage:
    """
    Central SQLite interface for session and settings storage in Guardian Daemon.
    """

    def get_user_settings(self, username: str):
        """
        Retrieve user settings from the database for the given username.

        Args:
            username (str): Nutzername

        Returns:
            dict | None: Einstellungen des Nutzers oder None
        """
        c = self.conn.cursor()
        c.execute("SELECT settings FROM user_settings WHERE username=?", (username,))
        row = c.fetchone()
        if row:
            return json.loads(row[0])
        return None

    def set_user_settings(self, username: str, settings: dict):
        """
        Store user settings in the database for the given username.

        Args:
            username (str): Nutzername
            settings (dict): Einstellungen
        """
        c = self.conn.cursor()
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
        self.conn = sqlite3.connect(self.db_path)
        self._init_db()

    def _init_db(self):
        """
        Initialize the SQLite database schema if not present.
        """
        try:
            with self.conn:
                self.conn.execute("PRAGMA journal_mode=WAL;")
                self.conn.execute("PRAGMA foreign_keys=ON;")
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
            print(f"[DB-ERROR] Fehler beim Initialisieren der Datenbank: {e}")

    def sync_config_to_db(self, config: dict):
        """
        Synchronize configuration data to the database.

        Args:
            config (dict): Konfigurationsdaten
        """
        # Defaults abgleichen
        if self.get_user_settings("default") is None:
            defaults = config.get("defaults", {})
            self.set_user_settings("default", defaults)
        # Users abgleichen
        for username, settings in config.get("users", {}).items():
            if self.get_user_settings(username) is None:
                # If settings are empty, save default
                if not settings:
                    settings = config.get("defaults", {})
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

    def get_sessions_for_user(self, username: str, since: Optional[float] = None):
        """
        Retrieve all sessions for a user, optionally since a specific time.

        Args:
            username (str): Username
            since (float, optional): Start time (Unix timestamp)

        Returns:
            list: List of sessions
        """
        c = self.conn.cursor()
        if since:
            c.execute(
                "SELECT * FROM sessions WHERE username=? AND start_time>=?",
                (username, since),
            )
        else:
            c.execute("SELECT * FROM sessions WHERE username=?", (username,))
        return c.fetchall()

    def get_all_usernames(self):
        """
        Return all usernames (except 'default') from the database.

        Returns:
            list: List of usernames
        """
        c = self.conn.cursor()
        c.execute("SELECT username FROM user_settings WHERE username != 'default'")
        return [row[0] for row in c.fetchall()]

    def delete_sessions_since(self, since: float):
        """
        Delete all sessions from the database since the given timestamp.

        Args:
            since (float): Startzeitpunkt (Unix-Timestamp)
        """
        c = self.conn.cursor()
        c.execute("DELETE FROM sessions WHERE start_time >= ?", (since,))
        self.conn.commit()

    def close(self):
        """
        Close the database connection.
        """
        self.conn.close()


# SQLite storage
