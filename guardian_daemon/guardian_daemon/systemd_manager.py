"""
Systemd manager for guardian-daemon.
Creates and manages systemd timers/units for daily reset and curfew.
"""

from pathlib import Path

SYSTEMD_PATH = Path("/etc/systemd/system")


class SystemdManager:
    """
    Manages systemd timers and units for daily reset and curfew enforcement.
    """

    def __init__(self):
        """
        Initialize the SystemdManager instance.
        """
        pass

    def create_daily_reset_timer(self, reset_time="03:00"):
        """
        Create a systemd timer and corresponding service unit for the daily quota reset.
        """
        timer_name = "guardian-daily-reset"
        service_unit = """
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
        # Write service unit
        with open(SYSTEMD_PATH / f"{timer_name}.service", "w") as f:
            f.write(service_unit)
        # Write timer unit
        with open(SYSTEMD_PATH / f"{timer_name}.timer", "w") as f:
            f.write(timer_unit)

        print(f"[SYSTEMD] Timer and service for daily reset created: {timer_name}")

    # TODO: Methods for curfew timer, reload, remove etc.


# systemd unit/timer management
