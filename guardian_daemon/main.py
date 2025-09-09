
import asyncio
import os
import logging
from guardian_daemon.policy import Policy
from guardian_daemon.storage import Storage
from guardian_daemon.pam_manager import PamManager
from guardian_daemon.sessions import SessionTracker
from guardian_daemon.systemd_manager import SystemdManager

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')

class GuardianDaemon:
	def __init__(self):
		self.policy = Policy()
		self.storage = Storage()
		self.pam = PamManager(self.policy)
		self.systemd = SystemdManager()
		self.tracker = SessionTracker(self.policy)
		self.last_config = self._get_config_snapshot()

	def _get_config_snapshot(self):
		"""Erzeuge einen Hash/Snapshot der aktuellen Policy-Konfiguration."""
		import hashlib, yaml
		return hashlib.sha256(yaml.dump(self.policy.data).encode()).hexdigest()

	async def periodic_reload(self):
		"""Prüft alle 5 Minuten auf Config-Änderungen und aktualisiert Timer/PAM."""
		while True:
			await asyncio.sleep(300)
			old_snapshot = self.last_config
			self.policy.reload()
			new_snapshot = self._get_config_snapshot()
			if new_snapshot != old_snapshot:
				logging.info("Config geändert, aktualisiere Timer und PAM-Regeln.")
				self.pam = PamManager(self.policy)
				self.pam.write_time_rules()
				reset_time = self.policy.data.get("reset_time", "03:00")
				self.systemd.create_daily_reset_timer(reset_time)
				self.last_config = new_snapshot

	def check_and_recover_reset(self):
		"""Prüft beim Start, ob der letzte Reset ausgeführt wurde, und holt ihn ggf. nach."""
		# TODO: Implementiere Logik, z.B. mit Timestamp in Storage oder Lockfile
		logging.info("Prüfe, ob Tagesreset nachgeholt werden muss (Stub).")

	async def run(self):
		# Initiales Setzen von PAM und Timer
		self.pam.write_time_rules()
		reset_time = self.policy.data.get("reset_time", "03:00")
		self.systemd.create_daily_reset_timer(reset_time)
		self.check_and_recover_reset()
		# Starte Session-Tracking und periodisches Reload parallel
		await asyncio.gather(
			self.tracker.run(),
			self.periodic_reload()
		)

def main():
	daemon = GuardianDaemon()
	asyncio.run(daemon.run())

if __name__ == "__main__":
	main()
# Entry point for guardian-daemon (systemd service)
