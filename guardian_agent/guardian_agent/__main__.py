import asyncio
import fcntl
import getpass
import os
import subprocess

import psutil
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method

from guardian_agent.lock_events import LockEventReporter
from guardian_agent.logging import get_logger

logger = get_logger("GuardianAgentMain")


class GuardianAgentInterface(ServiceInterface):
    """
    D-Bus service interface for Guardian Agent notifications.
    """

    def __init__(self, username):
        """
        Initialize the GuardianAgentInterface with the given username.
        """
        super().__init__("org.guardian.Agent")
        self.username = username

    @method()
    async def GetUsername(self) -> "s":  # noqa: F821
        """
        Return the username registered with this agent instance.
        """
        return self.username

    @method()
    async def NotifyUser(
        self, message: "s", category: "s" = "info"  # noqa: F821
    ) -> "s":  # noqa: F821
        """
        Show a desktop notification to the user with the given message and category.
        """
        categories = {
            "info": {"urgency": "low", "expire": "10000", "icon": "dialog-information"},
            "warning": {
                "urgency": "normal",
                "expire": "20000",
                "icon": "dialog-warning",
            },
            "critical": {
                "urgency": "critical",
                "expire": "60000",
                "icon": "dialog-error",
            },
        }
        cat = categories.get(category, categories["info"])
        subprocess.run(
            [
                "notify-send",
                "-a",
                "Guardian",
                "-i",
                cat["icon"],
                "-u",
                cat["urgency"],
                "-t",
                cat["expire"],
                message,
            ]
        )
        return ""


async def main():
    """
    Main entry point for the Guardian Agent. Registers the D-Bus interface and runs the event loop.
    """
    from dbus_next.constants import BusType

    # Create both system and session (user) bus connections
    system_bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    session_bus = await MessageBus(bus_type=BusType.SESSION).connect()
    username = getpass.getuser()

    obj_path = os.environ.get("GUARDIAN_AGENT_PATH")
    if not obj_path:
        lock_path = os.path.expanduser("~/.cache/guardian_agent_lock.txt")
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        session_num = None
        with open(lock_path, "a+") as lock_file:
            valid_lines = []
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            lock_file.seek(0)
            lines = lock_file.readlines()
            used = set()
            for line in lines:
                parts = line.strip().split()
                if len(parts) == 2:
                    pid = int(parts[1])
                    if psutil.pid_exists(pid):
                        used.add(int(parts[0]))
                        valid_lines.append(line)
            lock_file.seek(0)
            lock_file.truncate()
            for line in valid_lines:
                lock_file.write(line)
            for n in range(1, 100):
                if n not in used:
                    session_num = n
                    break
            lock_file.write(f"{session_num} {os.getpid()}\n")
            fcntl.flock(lock_file, fcntl.LOCK_UN)
        if session_num == 1:
            obj_path = "/org/guardian/Agent"
        else:
            obj_path = f"/org/guardian/Agent{session_num}"

    # Create all D-Bus sessions/interfaces at startup
    interface = GuardianAgentInterface(username)
    system_bus.export(obj_path, interface)
    # Request the well-known name so daemon can reach us
    await system_bus.request_name("org.guardian.Agent")
    logger.info(
        f"Guardian Agent listening for notifications for user: {username} on {obj_path} (name org.guardian.Agent)"
    )

    # Pass both buses to LockEventReporter and any other D-Bus components
    lock_reporter = LockEventReporter(
        obj_path, username, system_bus=system_bus, session_bus=session_bus
    )
    asyncio.create_task(lock_reporter.run())

    # Future: add other D-Bus services here and pass buses

    try:
        await asyncio.Future()  # run forever
    finally:
        lock_path = os.path.expanduser("~/.cache/guardian_agent_lock.txt")
        try:
            os.makedirs(os.path.dirname(lock_path), exist_ok=True)
            with open(lock_path, "r+") as lock_file:
                fcntl.flock(lock_file, fcntl.LOCK_EX)
                lines = lock_file.readlines()
                lock_file.seek(0)
                lock_file.truncate()
                for line in lines:
                    parts = line.strip().split()
                    if len(parts) == 2 and int(parts[1]) != os.getpid():
                        lock_file.write(line)
                fcntl.flock(lock_file, fcntl.LOCK_UN)
        except Exception as e:
            logger.error(f"[AGENT LOCK CLEANUP ERROR] {e}")


if __name__ == "__main__":
    asyncio.run(main())
