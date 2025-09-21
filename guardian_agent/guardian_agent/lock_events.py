"""
Guardian Agent: Tracks KDE screen lock/unlock events and sends them to the daemon via D-Bus IPC.
"""

import asyncio
import time

from guardian_agent.logging import get_logger

logger = get_logger("AgentLockEvents")


class LockEventReporter:
    def __init__(self, session_id, username, system_bus, session_bus):
        self.session_id = session_id
        self.username = username
        self.system_bus = system_bus
        self.session_bus = session_bus
        logger.info(
            f"LockEventReporter initialized: system_bus={getattr(self.system_bus, 'unique_name', repr(self.system_bus))}, session_bus={getattr(self.session_bus, 'unique_name', repr(self.session_bus))}"
        )

    async def send_lock_event(self, locked: bool):
        """
        Send lock/unlock event to daemon via D-Bus IPC.
        """
        timestamp = time.time()  # EPOCH timestamp
        try:
            logger.debug(
                f"Attempting to send lock event: system_bus unique_name={getattr(self.system_bus, 'unique_name', None)}, session_id={self.session_id}, username={self.username}, locked={locked}, timestamp={timestamp}"
            )
            logger.debug(
                "system_bus introspect: org.guardian.Daemon at /org/guardian/Daemon"
            )
            # Use system bus for daemon communication
            introspection = await self.system_bus.introspect(
                "org.guardian.Daemon", "/org/guardian/Daemon"
            )
            obj = self.system_bus.get_proxy_object(
                "org.guardian.Daemon", "/org/guardian/Daemon", introspection
            )
            iface = obj.get_interface("org.guardian.Daemon")
            logger.debug("Proxy object and interface created, calling LockEvent...")
            await iface.call_lock_event(
                self.session_id, self.username, locked, timestamp
            )
            logger.info(
                f"Sent lock event to daemon: session={self.session_id} user={self.username} locked={locked} ts={timestamp}"
            )
        except Exception as e:
            logger.error(f"Failed to send lock event to daemon: {e}")
            logger.debug(f"system_bus details: {repr(self.system_bus)}")

    async def listen_kde_locks(self):
        """
        Listen for KDE lock/unlock events via DBus and send to daemon.
        """
        # Use session bus for screensaver events
        for service, path, iface in [
            (
                "org.freedesktop.ScreenSaver",
                "/ScreenSaver",
                "org.freedesktop.ScreenSaver",
            ),
            ("org.kde.screensaver", "/ScreenSaver", "org.freedesktop.ScreenSaver"),
        ]:
            retries = 3
            for attempt in range(1, retries + 1):
                try:
                    introspection = await self.session_bus.introspect(service, path)
                    obj = self.session_bus.get_proxy_object(
                        service, path, introspection
                    )
                    screensaver_iface = obj.get_interface(iface)

                    def handler(active: bool):
                        logger.debug(f"Screen lock event: active={active}")
                        asyncio.create_task(self.send_lock_event(active))

                    screensaver_iface.on_active_changed(handler)
                    logger.info(f"Connected to {service} at {path} (attempt {attempt})")
                    break
                except Exception as e:
                    logger.warning(
                        f"Failed to connect to {service} at {path} (attempt {attempt}): {e}"
                    )
                    if attempt < retries:
                        await asyncio.sleep(2)
                    else:
                        logger.error(
                            f"Giving up on {service} at {path} after {retries} attempts."
                        )
        logger.info("Agent listening for KDE screen lock/unlock events.")

    async def run(self):
        await self.listen_kde_locks()
        while True:
            await asyncio.sleep(3600)
