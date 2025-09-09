# Entry point for guardian-daemon (systemd service)
import asyncio
import os
from guardian_daemon.policy import Policy
from guardian_daemon.storage import Storage
from guardian_daemon.pam_manager import PamManager
from guardian_daemon.sessions import SessionTracker

def main():
	# Policy initialisieren (ENV wird in Policy berücksichtigt)
	policy = Policy()
	# Storage initialisieren
	storage = Storage()
	# PAM-Regeln setzen
	pam = PamManager(policy)
	pam.write_time_rules()
	# Session-Tracking starten
	tracker = SessionTracker(policy)
	asyncio.run(tracker.run())

if __name__ == "__main__":
	main()
# Entry point for guardian-daemon (systemd service)
