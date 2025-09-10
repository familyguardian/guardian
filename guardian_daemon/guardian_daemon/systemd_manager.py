"""
Systemd-Manager für guardian-daemon
Erzeugt und verwaltet systemd-Timer/Units für Tagesreset und Curfew.
"""
import os
from pathlib import Path

SYSTEMD_PATH = Path("/etc/systemd/system")

class SystemdManager:
	def __init__(self):
		pass

	def create_daily_reset_timer(self, reset_time="03:00"):
		"""
		Erzeugt einen systemd-Timer und eine zugehörige Service-Unit für den Tagesreset.
		"""
		timer_name = "guardian-daily-reset"
		service_unit = f"""
[Unit]
Description=Guardian daily quota reset

[Service]
Type=oneshot
ExecStart=/usr/bin/guardianctl reset-quota
"""
		timer_unit = f"""
[Unit]
Description=Guardian daily quota reset timer

[Timer]
OnCalendar=*-*-* {reset_time}:00
Persistent=true

[Install]
WantedBy=timers.target
"""
		# Schreibe Service-Unit
		with open(SYSTEMD_PATH / f"{timer_name}.service", "w") as f:
			f.write(service_unit)
		# Schreibe Timer-Unit
		with open(SYSTEMD_PATH / f"{timer_name}.timer", "w") as f:
			f.write(timer_unit)
		print(f"[SYSTEMD] Timer und Service für Tagesreset erzeugt: {timer_name}")

	# TODO: Methoden für Curfew-Timer, Reload, Remove etc.
# systemd unit/timer management
