"""
Systemd manager for guardian-daemon.
Creates and manages systemd timers/units for daily reset and curfew.
"""

import asyncio
import re
from pathlib import Path

from guardian_daemon.logging import get_logger

logger = get_logger("SystemdManager")

SYSTEMD_PATH = Path("/etc/systemd/system")


def _is_valid_time_format(time_str):
    """Check if a string is in HH:MM format."""
    if not isinstance(time_str, str):
        return False
    return re.match(r"^(2[0-3]|[01]?[0-9]):([0-5]?[0-9])$", time_str)


class SystemdManager:
    """
    Manages systemd timers and units for daily reset and curfew enforcement.
    """

    def __init__(self):
        """
        Initialize the SystemdManager instance.
        """
        logger.debug("SystemdManager initialized.")

    def create_daily_reset_timer(self, reset_time="03:00"):
        """
        Create a systemd timer and corresponding service unit for the daily quota reset.
        """
        if not _is_valid_time_format(reset_time):
            logger.error(f"Invalid reset_time format: '{reset_time}'. Must be HH:MM.")
            return

        timer_name = "guardian-daily-reset"
        logger.debug(
            f"Preparing to create daily reset timer: {timer_name} at {reset_time}"
        )
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
        try:
            # Write service unit
            with open(SYSTEMD_PATH / f"{timer_name}.service", "w") as f:
                f.write(service_unit)
            logger.info(
                f"Service unit created: {SYSTEMD_PATH / f'{timer_name}.service'}"
            )
            # Write timer unit
            with open(SYSTEMD_PATH / f"{timer_name}.timer", "w") as f:
                f.write(timer_unit)
            logger.info(f"Timer unit created: {SYSTEMD_PATH / f'{timer_name}.timer'}")
            logger.info(f"Timer and service for daily reset created: {timer_name}")
        except Exception as e:
            logger.error(
                f"Failed to create daily reset timer/service: {e}", exc_info=True
            )

    def create_curfew_timer(self, start_time="22:00", end_time="06:00"):
        """
        Create a systemd timer and service unit for curfew enforcement.
        """
        if not _is_valid_time_format(start_time) or not _is_valid_time_format(end_time):
            logger.error(
                f"Invalid time format for curfew: start='{start_time}', end='{end_time}'. Must be HH:MM."
            )
            return

        timer_name = "guardian-curfew"
        logger.debug(
            f"Preparing to create curfew timer: {timer_name} from {start_time} to {end_time}"
        )
        service_unit = """
[Unit]
Description=Guardian curfew enforcement

[Service]
Type=oneshot
ExecStart=/usr/bin/guardianctl enforce-curfew
"""
        timer_unit = f"""
[Unit]
Description=Guardian curfew enforcement timer

[Timer]
OnCalendar=*-*-* {start_time}:00
OnCalendar=*-*-* {end_time}:00
Persistent=true

[Install]
WantedBy=timers.target
"""
        try:
            with open(SYSTEMD_PATH / f"{timer_name}.service", "w") as f:
                f.write(service_unit)
            logger.info(
                f"Service unit created: {SYSTEMD_PATH / f'{timer_name}.service'}"
            )
            with open(SYSTEMD_PATH / f"{timer_name}.timer", "w") as f:
                f.write(timer_unit)
            logger.info(f"Timer unit created: {SYSTEMD_PATH / f'{timer_name}.timer'}")
            logger.info(f"Curfew timer and service created: {timer_name}")
        except Exception as e:
            logger.error(f"Failed to create curfew timer/service: {e}", exc_info=True)

    async def reload_systemd(self):
        """
        Reload systemd units to apply changes.
        """
        logger.debug("Reloading systemd daemon...")
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl",
                "daemon-reload",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Use timeout to prevent hanging on slow/stuck systemctl command
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=10.0
                )
            except asyncio.TimeoutError:
                logger.error("systemctl daemon-reload timed out after 10 seconds")
                proc.kill()
                await proc.wait()
                return

            if proc.returncode != 0:
                logger.error(f"Failed to reload systemd daemon: {stderr.decode()}")
            else:
                logger.info("Systemd daemon reloaded.")
        except asyncio.TimeoutError:
            # Already handled above
            pass
        except Exception as e:
            logger.error(f"Failed to reload systemd daemon: {e}", exc_info=True)

    def remove_timer_and_service(self, timer_name):
        """
        Remove a systemd timer and service unit by name.
        """
        logger.debug(f"Attempting to remove timer and service: {timer_name}")
        timer_path = SYSTEMD_PATH / f"{timer_name}.timer"
        service_path = SYSTEMD_PATH / f"{timer_name}.service"
        removed = []
        for path in [timer_path, service_path]:
            try:
                if path.exists():
                    path.unlink()
                    logger.info(f"Removed: {path}")
                    removed.append(str(path))
                else:
                    logger.warning(f"File not found: {path}")
            except Exception as e:
                logger.error(f"Failed to remove {path}: {e}", exc_info=True)
        if removed:
            logger.info(f"Successfully removed: {', '.join(removed)}")
        else:
            logger.warning(f"No files removed for timer/service: {timer_name}")


# systemd unit/timer management
