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
	"""
	Überwacht und speichert Nutzersessions, prüft Quota und Curfew.
	Bindet sich an systemd-logind via DBus.
	"""
	def __init__(self, policy: Policy):
		"""
		Initialisiert den SessionTracker.

		Args:
			policy (Policy): Policy-Instanz
		"""
		self.policy = policy
		self.storage = Storage()
		self.active_sessions = {}  # session_id -> {uid, username, start_time}

	def handle_login(self, session_id, uid, username):
		"""
		Registriert eine neue Session beim Login.

		Args:
			session_id (str): Session-ID
			uid (int): User-ID
			username (str): Nutzername
		"""
		self.active_sessions[session_id] = {
			"uid": uid,
			"username": username,
			"start_time": time.monotonic()
		}
		print(f"Login: {username} (UID {uid}) Session {session_id}")

	def handle_logout(self, session_id):
		"""
		Beendet eine Session beim Logout und speichert sie in der Datenbank.

		Args:
			session_id (str): Session-ID
		"""
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
		"""
		Summiert alle Sessions seit dem letzten Reset-Zeitpunkt und prüft gegen das Tageskontingent.
		Gibt True zurück, wenn noch Zeit übrig ist, sonst False.

		Args:
			username (str): Nutzername

		Returns:
			bool: True wenn noch Zeit übrig, False wenn Limit erreicht
		"""
		user_policy = self.policy.get_user_policy(username)
		if user_policy is None:
			return True  # Nutzer wird nicht überwacht
		quota = user_policy.get("daily_quota_minutes")
		if quota is None:
			quota = self.policy.get_default("daily_quota_minutes")

		import datetime
		reset_time = self.policy.data.get("reset_time", "03:00")
		now = datetime.datetime.now(datetime.timezone.utc).astimezone()
		reset_hour, reset_minute = map(int, reset_time.split(":"))
		today_reset = now.replace(hour=reset_hour, minute=reset_minute, second=0, microsecond=0)
		if now < today_reset:
			last_reset = today_reset - datetime.timedelta(days=1)
		else:
			last_reset = today_reset

		sessions = self.storage.get_sessions_for_user(username, since=last_reset.timestamp())
		total_minutes = sum((s[6] for s in sessions)) / 60  # s[6] = duration (Sekunden)

		for session in self.active_sessions.values():
			if session["username"] == username:
				total_minutes += (time.monotonic() - session["start_time"]) / 60

		return total_minutes < quota

	async def run(self):
		"""
		Startet das Session-Tracking und bindet sich an systemd-logind via DBus.
		"""
		bus = await MessageBus().connect()
		introspection = await bus.introspect('org.freedesktop.login1', '/org/freedesktop/login1')
		obj = bus.get_proxy_object('org.freedesktop.login1', '/org/freedesktop/login1', introspection)
		manager = obj.get_interface('org.freedesktop.login1.Manager')

		def session_new_handler(session_id, uid):
			username = self._get_username(uid)
			self.handle_login(session_id, uid, username)

		def session_removed_handler(session_id):
			self.handle_logout(session_id)

		manager.on_session_new(session_new_handler)
		manager.on_session_removed(session_removed_handler)

		print("SessionTracker läuft. Warten auf Logins/Logouts...")
		while True:
			await asyncio.sleep(3600)

	def _get_username(self, uid):
		"""
		Holt den Nutzernamen zu einer UID.

		Args:
			uid (int): User-ID

		Returns:
			str: Nutzername
		"""
		import pwd
		try:
			return pwd.getpwuid(uid).pw_name
		except Exception:
			return str(uid)

	def __init__(self, policy: Policy):
		self.policy = policy
		self.storage = Storage()
		self.active_sessions = {}  # session_id -> {uid, username, start_time}

	# DB-Initialisierung erfolgt zentral in Storage

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
