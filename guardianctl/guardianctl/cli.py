# CLI tool for guardianctl (Typer)

"""
CLI-Tool für guardianctl (Typer).
"""
import json
import socket
from typing import Dict

import typer

app = typer.Typer()

IPC_SOCKET = "/run/guardian-daemon.sock"


def ipc_call(command, arg=None):
    """
    Send an IPC command to the daemon and return the response.
    """
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        try:
            s.connect(IPC_SOCKET)

            # Format command with argument if present
            message = command
            if arg:
                message = f"{command} {arg}"

            # Send message length then message
            message_data = message.encode()
            s.sendall(len(message_data).to_bytes(4, "big"))
            s.sendall(message_data)

            # Read response length then response
            len_data = s.recv(4)
            if not len_data:
                return "No response from daemon"
            msg_len = int.from_bytes(len_data, "big")

            resp = s.recv(msg_len)
            return resp.decode()
        except ConnectionRefusedError:
            return json.dumps(
                {
                    "error": f"Cannot connect to daemon socket at {IPC_SOCKET}. Is the daemon running?"
                }
            )
        except FileNotFoundError:
            return json.dumps(
                {"error": f"Socket {IPC_SOCKET} not found. Is the daemon running?"}
            )
        except Exception as e:
            return json.dumps({"error": f"Communication error: {str(e)}"})


def get_available_commands() -> Dict:
    """
    Query the daemon for available commands and their descriptions.
    """
    try:
        response = ipc_call("describe_commands")
        return json.loads(response)
    except Exception as e:
        typer.echo(f"Error fetching available commands: {e}", err=True)
        return {}


# Create dynamic commands based on available IPC commands
def register_dynamic_commands():
    """
    Register commands dynamically based on what the IPC server supports.
    """
    commands = get_available_commands()

    if not commands or isinstance(commands, str) or "error" in commands:
        # If we couldn't get commands from the daemon, register diagnostic commands
        typer.secho(
            "\n⚠️  WARNING: Cannot connect to the Guardian daemon! ⚠️",
            fg="yellow",
            err=True,
        )
        typer.echo(
            "Commands for normal operation are not available. Registering diagnostic commands instead.",
            err=True,
        )
        typer.echo(
            "Use 'status', 'logs', or 'socket-check' commands to diagnose the issue with the daemon.",
            err=True,
        )
        typer.echo("", err=True)  # Empty line for better formatting
        register_basic_commands()
        return

    for cmd_name, cmd_info in commands.items():
        description = cmd_info.get("description", "")
        params = cmd_info.get("params", [])

        # Skip internal/special commands
        if cmd_name == "describe_commands":
            continue

        # Convert underscores to hyphens for command names (CLI convention)
        cli_cmd_name = cmd_name.replace("_", "-")

        # Special case handling for common commands to make them more user-friendly
        if cmd_name == "list_kids":
            cli_cmd_name = "show-users"
            description = "Show all users in the guardian system."

        # Create the command
        create_command(app, cli_cmd_name, cmd_name, description, params)

    # Add help command if not present
    if "help" not in commands:

        @app.command()
        def help():
            """Show help for all commands."""
            # Use typer's built-in help

            # Pass --help to the app (which will cause it to print help and exit)
            # We'll redirect the help output to be part of our command
            import io
            from contextlib import redirect_stdout

            f = io.StringIO()
            with redirect_stdout(f):
                try:
                    app(["--help"], standalone_mode=False)
                except SystemExit:
                    pass

            typer.echo(f.getvalue())


def create_command(app_instance, cli_name, ipc_name, description, params):
    """
    Dynamically create and register a command with the Typer app instance.
    """
    # For commands with parameters
    if params and params[0] != "_":

        @app_instance.command(name=cli_name)
        def cmd_with_param(
            param: str = typer.Argument(..., help=f"Parameter for {cli_name}")
        ):
            """Dynamic command handler with parameter."""
            result = ipc_call(ipc_name, param)
            try:
                # Try to pretty-print JSON responses
                parsed = json.loads(result)
                typer.echo(json.dumps(parsed, indent=2))
            except (json.JSONDecodeError, ValueError):
                # If not JSON, just print as-is
                typer.echo(result)

        # Set the docstring
        cmd_with_param.__doc__ = description

    # For commands without parameters
    else:

        @app_instance.command(name=cli_name)
        def cmd_without_param():
            """Dynamic command handler without parameter."""
            result = ipc_call(ipc_name)
            try:
                # Try to pretty-print JSON responses
                parsed = json.loads(result)
                typer.echo(json.dumps(parsed, indent=2))
            except (json.JSONDecodeError, ValueError):
                # If not JSON, just print as-is
                typer.echo(result)

        # Set the docstring
        cmd_without_param.__doc__ = description


def register_basic_commands():
    """
    Register diagnostic commands for when we can't connect to the daemon.
    These commands help diagnose the issue with the daemon connection.
    """
    import os
    import subprocess
    from datetime import datetime

    @app.command(name="status")
    def daemon_status():
        """Check the status of the guardian daemon service."""
        typer.echo("Checking Guardian daemon status...\n")

        # Run systemctl command to check daemon status
        try:
            # First try as normal user with sudo
            cmd = ["sudo", "systemctl", "status", "guardian_daemon.service"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)

            if result.returncode == 0:
                typer.echo(result.stdout)
            else:
                typer.echo(f"Error getting daemon status: {result.stderr}")
                typer.echo("\nTrying without sudo...")

                # Try without sudo as fallback
                cmd = ["systemctl", "status", "guardian_daemon.service"]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    typer.echo(result.stdout)
                else:
                    typer.echo("Failed to get daemon status with or without sudo.")
        except subprocess.TimeoutExpired:
            typer.echo("Command timed out while checking daemon status.")
        except Exception as e:
            typer.echo(f"Error checking daemon status: {str(e)}")

    @app.command(name="logs")
    def daemon_logs(lines: int = 20):
        """Show recent logs from the guardian daemon."""
        typer.echo(f"Showing last {lines} lines of guardian daemon logs...\n")

        try:
            # Try with sudo first
            cmd = [
                "sudo",
                "journalctl",
                "-u",
                "guardian_daemon.service",
                "-n",
                str(lines),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)

            if result.returncode == 0:
                typer.echo(result.stdout)
            else:
                typer.echo(f"Error fetching daemon logs: {result.stderr}")
                typer.echo("\nTrying without sudo...")

                # Try without sudo as fallback
                cmd = ["journalctl", "-u", "guardian_daemon.service", "-n", str(lines)]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    typer.echo(result.stdout)
                else:
                    typer.echo("Failed to get daemon logs with or without sudo.")
        except subprocess.TimeoutExpired:
            typer.echo("Command timed out while fetching daemon logs.")
        except Exception as e:
            typer.echo(f"Error fetching daemon logs: {str(e)}")

    @app.command(name="socket-check")
    def check_socket():
        """Check if the guardian daemon socket exists and has proper permissions."""
        typer.echo(f"Checking daemon socket at {IPC_SOCKET}...\n")

        if not os.path.exists(IPC_SOCKET):
            typer.echo(f"❌ Socket file not found: {IPC_SOCKET}")
            typer.echo("The daemon may not be running.")
            return

        try:
            # Get socket file stats
            stats = os.stat(IPC_SOCKET)
            perms = oct(stats.st_mode)[-3:]  # Last 3 digits of octal permissions

            typer.echo(f"✅ Socket file exists: {IPC_SOCKET}")
            typer.echo(f"   - Owner: {stats.st_uid}")
            typer.echo(f"   - Group: {stats.st_gid}")
            typer.echo(f"   - Permissions: {perms}")
            typer.echo(f"   - Last modified: {datetime.fromtimestamp(stats.st_mtime)}")

            # Try to connect to socket
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(2)  # Set 2 second timeout
                s.connect(IPC_SOCKET)
                typer.echo("✅ Socket connection successful!")
                s.close()
            except ConnectionRefusedError:
                typer.echo(
                    "❌ Connection refused. The daemon may not be listening on this socket."
                )
            except socket.timeout:
                typer.echo("❌ Connection timed out. The daemon is not responding.")
            except Exception as e:
                typer.echo(f"❌ Socket connection error: {str(e)}")
        except Exception as e:
            typer.echo(f"Error checking socket: {str(e)}")

    @app.command(name="restart-daemon")
    def restart_daemon():
        """Attempt to restart the guardian daemon service."""
        typer.echo("Attempting to restart the Guardian daemon...")

        try:
            # First try with sudo
            cmd = ["sudo", "systemctl", "restart", "guardian_daemon.service"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)

            if result.returncode == 0:
                typer.echo("✅ Guardian daemon restarted successfully!")
                typer.echo("\nChecking daemon status after restart...")

                # Check status after restart
                status_cmd = ["sudo", "systemctl", "status", "guardian_daemon.service"]
                status_result = subprocess.run(
                    status_cmd, capture_output=True, text=True, timeout=5
                )
                typer.echo(status_result.stdout)
            else:
                typer.echo(f"❌ Failed to restart daemon: {result.stderr}")
                typer.echo("You may not have the necessary permissions.")
        except subprocess.TimeoutExpired:
            typer.echo("Command timed out while restarting daemon.")
        except Exception as e:
            typer.echo(f"Error restarting daemon: {str(e)}")


# Register commands at module load time
register_dynamic_commands()

if __name__ == "__main__":
    app()
