"""
Guardian Agent: Tracks KDE screen lock/unlock events and sends them to the daemon via D-Bus IPC.
"""

import asyncio
import time

from dbus_next.aio import MessageBus

from guardian_agent.logging import get_logger

logger = get_logger("AgentLockEvents")


class LockEventReporter:
    def __init__(self, session_id, username):
        self.session_id = session_id
        self.username = username
        self.bus = None

    async def send_lock_event(self, locked: bool):
        """
        Send lock/unlock event to daemon via D-Bus IPC.
        """
        timestamp = time.monotonic()
        # TODO: Replace with actual D-Bus IPC to daemon
        logger.info(
            f"Sending lock event: session={self.session_id} user={self.username} locked={locked} ts={timestamp}"
        )
        # Example: call daemon's D-Bus method: LockEvent(session_id, username, locked, timestamp)

    async def listen_kde_locks(self):
        """
        Listen for KDE lock/unlock events via DBus and send to daemon.
        """
        self.bus = await MessageBus().connect()
        for service, path, iface in [
            (
                "org.freedesktop.ScreenSaver",
                "/ScreenSaver",
                "org.freedesktop.ScreenSaver",
            ),
            ("org.kde.screensaver", "/ScreenSaver", "org.freedesktop.ScreenSaver"),
        ]:
            introspection = await self.bus.introspect(service, path)
            obj = self.bus.get_proxy_object(service, path, introspection)
            screensaver_iface = obj.get_interface(iface)

            def handler(active: bool):
                logger.debug(f"Screen lock event: active={active}")
                asyncio.create_task(self.send_lock_event(active))

            screensaver_iface.on_active_changed(handler)
        logger.info("Agent listening for KDE screen lock/unlock events.")

    async def run(self):
        await self.listen_kde_locks()
        while True:
            await asyncio.sleep(3600)
