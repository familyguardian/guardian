import asyncio
import getpass
import os
import subprocess

from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method


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
    async def GetUsername(self) -> str:
        """
        Return the username registered with this agent instance.
        """
        return self.username

    @method()
    async def NotifyUser(self, message: str, category: str = "info"):
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


async def main():
    """
    Main entry point for the Guardian Agent. Registers the D-Bus interface and runs the event loop.
    """
    bus = await MessageBus().connect()
    username = getpass.getuser()
    import fcntl

    obj_path = os.environ.get("GUARDIAN_AGENT_PATH")
    if not obj_path:
        # Automatic session numbering with cleanup of orphaned entries
        import psutil

        lock_path = os.path.join(os.path.dirname(__file__), "agent_path_lock.txt")
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
                    # Check if PID exists
                    if psutil.pid_exists(pid):
                        used.add(int(parts[0]))
                        valid_lines.append(line)
            # Write back only valid entries
            lock_file.seek(0)
            lock_file.truncate()
            for line in valid_lines:
                lock_file.write(line)
            # Find the lowest available number
            for n in range(1, 100):
                if n not in used:
                    session_num = n
                    break
            # Write own session number and PID
            lock_file.write(f"{session_num} {os.getpid()}\n")
            fcntl.flock(lock_file, fcntl.LOCK_UN)
        if session_num == 1:
            obj_path = "/org/guardian/Agent"
        else:
            obj_path = f"/org/guardian/Agent{session_num}"
    interface = GuardianAgentInterface(username)
    bus.export(obj_path, interface)
    print(
        f"Guardian Agent listening for notifications for user: {username} on {obj_path}"
    )
    try:
        await asyncio.Future()  # run forever
    finally:
        # Remove lock entry on exit
        lock_path = os.path.join(os.path.dirname(__file__), "agent_path_lock.txt")
        try:
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
            print(f"[AGENT LOCK CLEANUP ERROR] {e}")


if __name__ == "__main__":
    asyncio.run(main())
