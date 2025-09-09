"""
Policy-Loader für guardian-daemon
Lädt und validiert die Einstellungen aus einer YAML-Konfigurationsdatei.
"""
import yaml
from pathlib import Path
from typing import Any, Dict, Optional

class Policy:
	def __init__(self, config_path: str = "config.yaml"):
		self.config_path = Path(config_path)
		self.data: Dict[str, Any] = {}
		self.load()

	def load(self):
		if not self.config_path.exists():
			raise FileNotFoundError(f"Policy-Datei nicht gefunden: {self.config_path}")
		with open(self.config_path, "r") as f:
			self.data = yaml.safe_load(f)

	def get_user_policy(self, username: str) -> Optional[Dict[str, Any]]:
		users = self.data.get("users", {})
		return users.get(username)

	def get_default(self, key: str) -> Any:
		defaults = self.data.get("defaults", {})
		return defaults.get(key)

	def get_timezone(self) -> str:
		return self.data.get("timezone", "Europe/Berlin")

	def reload(self):
		self.load()

# Beispiel für die Nutzung
if __name__ == "__main__":
	policy = Policy("config.yaml")
	print("Timezone:", policy.get_timezone())
	print("Default Quota:", policy.get_default("daily_quota_minutes"))
	print("Policy für kid1:", policy.get_user_policy("kid1"))
# Policy models (pydantic)
