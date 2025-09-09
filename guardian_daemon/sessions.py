"""
Session-Tracking für guardian-daemon
Überwacht Logins/Logouts via systemd-logind (DBus), misst Nutzungszeit und prüft Quota/Curfew.
Speichert Daten in SQLite.
"""
import time
import threading
import asyncio
from dbus_next.aio import MessageBus
from dbus_next import Variant
from dbus_next.constants import MessageType
from guardian_daemon.storage import Storage
from guardian_daemon.policy import Policy


class SessionTracker:
	def __init__(self, policy: Policy):
		self.policy = policy
		self.storage = Storage()
		self.active_sessions = {}  # session_id -> {uid, username, start_time}

	def handle_login(self, session_id, uid, username):
		self.active_sessions[session_id] = {
			"uid": uid,
			"username": username,
			"start_time": time.monotonic()
		}
		print(f"Login: {username} (UID {uid}) Session {session_id}")

	def handle_logout(self, session_id):
		session = self.active_sessions.pop(session_id, None)
		if session:
			end_time = time.monotonic()
			duration = end_time - session["start_time"]
			self.storage.add_session(
				session_id,
				session["username"],
				session["uid"],
				session["start_time"],
				end_time,
				duration
			)
			print(f"Logout: {session['username']} Session {session_id} Dauer: {duration:.1f}s")

	def check_quota(self, username):
		quota = self.policy.get_user_policy(username)
		if not quota:
			quota = self.policy.get_default("daily_quota_minutes")
		# TODO: Summiere alle Sessions des Tages und prüfe gegen quota
		# Rückgabe: True wenn noch Zeit übrig, False wenn Limit erreicht
		return True

	async def run(self):
		bus = await MessageBus().connect()
		introspection = await bus.introspect('org.freedesktop.login1', '/org/freedesktop/login1')
		obj = bus.get_proxy_object('org.freedesktop.login1', '/org/freedesktop/login1', introspection)
		manager = obj.get_interface('org.freedesktop.login1.Manager')

		def session_new_handler(session_id, uid):
			username = self._get_username(uid)
			self.handle_login(session_id, uid, username)

		def session_removed_handler(session_id):
			self.handle_logout(session_id)

		# Signal-Handler registrieren
		manager.on_session_new(session_new_handler)
		manager.on_session_removed(session_removed_handler)

		print("SessionTracker läuft. Warten auf Logins/Logouts...")
		while True:
			await asyncio.sleep(3600)

	def _get_username(self, uid):
		import pwd
		try:
			return pwd.getpwuid(uid).pw_name
		except Exception:
			return str(uid)

	def __init__(self, policy: Policy):
		self.policy = policy
		self.storage = Storage()
		self.active_sessions = {}  # session_id -> {uid, username, start_time}

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

	def handle_login(self, session_id, uid, username):
		self.active_sessions[session_id] = {
			"uid": uid,
			"username": username,
			"start_time": time.monotonic()
		}
		print(f"Login: {username} (UID {uid}) Session {session_id}")

	def handle_logout(self, session_id):
		session = self.active_sessions.pop(session_id, None)
		if session:
			end_time = time.monotonic()
			duration = end_time - session["start_time"]
			self.storage.add_session(
				session_id,
				session["username"],
				session["uid"],
				session["start_time"],
				end_time,
				duration
			)
			print(f"Logout: {session['username']} Session {session_id} Dauer: {duration:.1f}s")

	def check_quota(self, username):
		quota = self.policy.get_user_policy(username)
		if not quota:
			quota = self.policy.get_default("daily_quota_minutes")
		# TODO: Summiere alle Sessions des Tages und prüfe gegen quota
		# Rückgabe: True wenn noch Zeit übrig, False wenn Limit erreicht
		return True

	async def run(self):
		bus = await MessageBus().connect()
		introspection = await bus.introspect('org.freedesktop.login1', '/org/freedesktop/login1')
		obj = bus.get_proxy_object('org.freedesktop.login1', '/org/freedesktop/login1', introspection)
		manager = obj.get_interface('org.freedesktop.login1.Manager')

		def session_new_handler(session_id, uid):
			username = self._get_username(uid)
			self.handle_login(session_id, uid, username)

		def session_removed_handler(session_id):
			self.handle_logout(session_id)

		# Signal-Handler registrieren
		manager.on_session_new(session_new_handler)
		manager.on_session_removed(session_removed_handler)

		print("SessionTracker läuft. Warten auf Logins/Logouts...")
		while True:
			await asyncio.sleep(3600)

	def _get_username(self, uid):
		import pwd
		try:
			return pwd.getpwuid(uid).pw_name
		except Exception:
			return str(uid)

if __name__ == "__main__":
	import sys
	policy = Policy("config.yaml")
	tracker = SessionTracker(policy)
	asyncio.run(tracker.run())
# logind watcher
