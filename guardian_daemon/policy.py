"""
Policy-Loader für guardian-daemon
Lädt und validiert die Einstellungen aus einer YAML-Konfigurationsdatei.
"""
import yaml
from pathlib import Path
from typing import Any, Dict, Optional


from guardian_daemon.storage import Storage

class Policy:
	"""
	Lädt und verwaltet die Guardian-Policy aus einer YAML-Konfigurationsdatei und synchronisiert sie mit der Datenbank.
	Stellt Methoden zum Zugriff auf Nutzer- und Default-Settings bereit.
	"""
	def __init__(self, config_path: str = None, db_path: str = None):
		"""
		Initialisiert die Policy-Instanz.
		Liest die Konfiguration aus einer YAML-Datei und synchronisiert sie mit der Datenbank.

		Args:
			config_path (str, optional): Pfad zur YAML-Konfigurationsdatei.
			db_path (str, optional): Pfad zur SQLite-Datenbank.
		"""
		import os
		env_path = os.environ.get("GUARDIAN_DAEMON_CONFIG")
		self.config_path = Path(config_path or env_path or "config.yaml")
		import yaml
		with open(self.config_path, "r") as f:
			self.data = yaml.safe_load(f)
		self.db_path = db_path or self.data.get("db_path", "/var/lib/guardian/guardian.sqlite")
		self.storage = Storage(self.db_path)
		# Sync config to DB beim Start
		self.storage.sync_config_to_db(self.data)

	def get_user_policy(self, username: str) -> Optional[Dict[str, Any]]:
		"""
		Gibt die Policy-Einstellungen für einen bestimmten Nutzer zurück.

		Args:
			username (str): Nutzername

		Returns:
			dict | None: Die Einstellungen des Nutzers oder None, falls nicht vorhanden.
		"""
		return self.storage.get_user_settings(username)

	def get_default(self, key: str) -> Any:
		"""
		Gibt einen Default-Wert aus der Policy zurück.

		Args:
			key (str): Name des Default-Keys

		Returns:
			Any: Der Default-Wert oder None
		"""
		defaults = self.storage.get_user_settings('default')
		if defaults:
			return defaults.get(key)
		return None

	def get_timezone(self) -> str:
		"""
		Gibt die konfigurierte Zeitzone zurück.

		Returns:
			str: Zeitzone (z.B. "Europe/Berlin")
		"""
		return self.data.get("timezone", "Europe/Berlin")

	def reload(self):
		"""
		Lädt die Policy-Konfiguration neu und synchronisiert sie mit der Datenbank.
		"""
		import yaml
		with open(self.config_path, "r") as f:
			self.data = yaml.safe_load(f)
		self.storage.sync_config_to_db(self.data)

# Beispiel für die Nutzung
if __name__ == "__main__":
	policy = Policy("config.yaml")
	print("Timezone:", policy.get_timezone())
	print("Default Quota:", policy.get_default("daily_quota_minutes"))
	print("Policy für kid1:", policy.get_user_policy("kid1"))
# Policy models (pydantic)
