"""
PAM-Manager für guardian-daemon
Verwaltet Login-Zeitfenster für Kinder über /etc/security/time.conf
"""
import os
from pathlib import Path
from guardian_daemon.policy import Policy

TIME_CONF_PATH = Path("/etc/security/time.conf")

class PamManager:
	def __init__(self, policy: Policy):
		self.policy = policy

	def write_time_rules(self):
		"""
		Schreibt die Zeitregeln für alle Kinder gemäß Policy in /etc/security/time.conf
		"""
		rules = self._generate_rules()
		# Backup der bestehenden Datei
		if TIME_CONF_PATH.exists():
			os.rename(TIME_CONF_PATH, TIME_CONF_PATH.with_suffix(".conf.bak"))
		with open(TIME_CONF_PATH, "w") as f:
			f.write("# Managed by guardian-daemon\n")
			for rule in rules:
				f.write(rule + "\n")

	def _generate_rules(self):
		"""
		Erzeugt die PAM-Zeitregeln aus der Policy
		"""
		rules = []
		users = self.policy.data.get("users", {})
		for username, user_policy in users.items():
			curfew = user_policy.get("curfew", self.policy.get_default("curfew"))
			# Beispiel: weekdays: "08:00-20:00"
			if curfew:
				for day, times in curfew.items():
					# PAM time.conf Syntax: <service>;<ttys>;<users>;<day>;<start>-<end>
					rules.append(f"login;*;{username};{day};{times}")
		return rules

	def remove_time_rules(self):
		"""
		Entfernt die von guardian-daemon gesetzten Zeitregeln
		"""
		if TIME_CONF_PATH.exists():
			with open(TIME_CONF_PATH, "r") as f:
				lines = f.readlines()
			with open(TIME_CONF_PATH, "w") as f:
				for line in lines:
					if not line.startswith("login;*") and not line.startswith("# Managed by guardian-daemon"):
						f.write(line)
# PAM time.conf block management
