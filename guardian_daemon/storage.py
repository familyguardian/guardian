"""
Zentrale SQLite-Schnittstelle für guardian-daemon
Stellt Funktionen für Session-Handling und spätere Erweiterungen bereit.
"""
import sqlite3
from pathlib import Path
from typing import Optional


import json

class Storage:
	def __init__(self, db_path: str):
		self.db_path = db_path
		self.conn = sqlite3.connect(self.db_path)
		self._init_db()

	def _init_db(self):
		c = self.conn.cursor()
		c.execute("""
			CREATE TABLE IF NOT EXISTS sessions (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				session_id TEXT,
				username TEXT,
				uid INTEGER,
				start_time REAL,
				end_time REAL,
				duration REAL
			)
		""")
		c.execute("""
			CREATE TABLE IF NOT EXISTS user_settings (
				username TEXT PRIMARY KEY,
				settings TEXT
			)
		""")
		self.conn.commit()

	def get_user_settings(self, username: str):
		c = self.conn.cursor()
		c.execute("SELECT settings FROM user_settings WHERE username=?", (username,))
		row = c.fetchone()
		if row:
			return json.loads(row[0])
		return None

	def set_user_settings(self, username: str, settings: dict):
		c = self.conn.cursor()
		c.execute("INSERT OR REPLACE INTO user_settings (username, settings) VALUES (?, ?)", (username, json.dumps(settings)))
		self.conn.commit()

	def sync_config_to_db(self, config: dict):
		# Defaults abgleichen
		if self.get_user_settings('default') is None:
			defaults = config.get('defaults', {})
			self.set_user_settings('default', defaults)
		# Users abgleichen
		for username, settings in config.get('users', {}).items():
			if self.get_user_settings(username) is None:
				# Falls settings leer, speichere default
				if not settings:
					settings = config.get('defaults', {})
				self.set_user_settings(username, settings)

	def _init_db(self):
		c = self.conn.cursor()
		c.execute("""
			CREATE TABLE IF NOT EXISTS sessions (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				session_id TEXT,
				username TEXT,
				uid INTEGER,
				start_time REAL,
				end_time REAL,
				duration REAL
			)
		""")
		self.conn.commit()

	def add_session(self, session_id: str, username: str, uid: int, start_time: float, end_time: float, duration: float):
		c = self.conn.cursor()
		c.execute("""
			INSERT INTO sessions (session_id, username, uid, start_time, end_time, duration)
			VALUES (?, ?, ?, ?, ?, ?)
		""", (session_id, username, uid, start_time, end_time, duration))
		self.conn.commit()

	def get_sessions_for_user(self, username: str, since: Optional[float] = None):
		c = self.conn.cursor()
		if since:
			c.execute("SELECT * FROM sessions WHERE username=? AND start_time>=?", (username, since))
		else:
			c.execute("SELECT * FROM sessions WHERE username=?", (username,))
		return c.fetchall()

	def close(self):
		self.conn.close()
# SQLite storage
