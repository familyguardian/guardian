import asyncio
import getpass
import os

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

        try:
            proc = await asyncio.create_subprocess_exec(
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
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                logger.error(
                    f"notify-send failed with code {proc.returncode}: {stderr.decode().strip()}"
                )
        except FileNotFoundError:
            logger.error("`notify-send` command not found. Please install it.")
        except Exception as e:
            logger.error(f"An error occurred while sending notification: {e}")

        return ""


async def main():
    """
    Main entry point for the Guardian Agent. Registers the D-Bus interface and runs the event loop.
    """
    from dbus_next.constants import BusType

    # A single agent process should run per user session.
    # We use a lock file to ensure this.
    username = getpass.getuser()
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    lock_path = os.path.join(runtime_dir, "guardian_agent.lock")

    os.makedirs(runtime_dir, exist_ok=True)
    lock_file = open(lock_path, "w")
    try:
        import fcntl

        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, ImportError):
        logger.error("Another instance of Guardian Agent is already running. Exiting.")
        return

    # Create a unique bus name for this agent instance
    pid = os.getpid()
    bus_name = f"org.guardian.Agent.{username}.pid{pid}"

    logger.info(f"Guardian Agent starting up, PID: {pid}, User: {username}")
    logger.info(f"Requesting D-Bus name: {bus_name}")

    try:
        agent_iface = GuardianAgentInterface(username)
        # Get the system bus
        system_bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        await system_bus.request_name(bus_name)
        system_bus.export("/org/guardian/Agent", agent_iface)

        # Also get the session bus for screen lock events
        session_bus = await MessageBus(bus_type=BusType.SESSION).connect()

        # Pass both buses to LockEventReporter and any other D-Bus components
        lock_reporter = LockEventReporter(
            "/org/guardian/Agent",
            username,
            system_bus=system_bus,
            session_bus=session_bus,
        )
        asyncio.create_task(lock_reporter.run())

        await asyncio.Future()  # run forever
    finally:
        # Release the lock and clean up the file on exit.
        import fcntl

        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()
        try:
            os.remove(lock_path)
        except OSError as e:
            logger.error(f"Error removing lock file: {e}")


if __name__ == "__main__":
    asyncio.run(main())
