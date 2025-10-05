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
            "Use 'status', 'logs', 'socket-check', 'config-check', or 'restart-daemon' commands to diagnose the issue with the daemon.",
            err=True,
        )
        typer.echo("", err=True)  # Empty line for better formatting
        register_diagnostic_commands()
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


def register_diagnostic_commands():
    """
    Register diagnostic commands for when we can't connect to the daemon.
    These commands help diagnose and troubleshoot issues with the daemon connection.
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
            # Try different approaches for systemctl
            cmds = [
                [
                    "systemctl",
                    "status",
                    "guardian_daemon.service",
                ],  # Try without sudo first
                [
                    "sudo",
                    "-n",
                    "systemctl",
                    "status",
                    "guardian_daemon.service",
                ],  # sudo with non-interactive flag
                [
                    "sudo",
                    "systemctl",
                    "status",
                    "guardian_daemon.service",
                ],  # regular sudo (may prompt)
            ]

            success = False
            for cmd in cmds:
                try:
                    typer.echo(f"Running: {' '.join(cmd)}")
                    result = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0 or "Active:" in result.stdout:
                        typer.echo(result.stdout)
                        success = True
                        break
                except Exception as e:
                    typer.echo(f"Command failed: {e}")
                    continue

            if not success:
                # As a last resort, try checking if process is running
                try:
                    ps_cmd = [
                        "ps",
                        "-ef",
                        "|",
                        "grep",
                        "guardian_daemon",
                        "|",
                        "grep",
                        "-v",
                        "grep",
                    ]
                    typer.echo("Checking if daemon process is running...")
                    ps_result = subprocess.run(
                        " ".join(ps_cmd), shell=True, capture_output=True, text=True
                    )
                    if "guardian_daemon" in ps_result.stdout:
                        typer.echo("✅ Guardian daemon process appears to be running:")
                        typer.echo(ps_result.stdout)
                    else:
                        typer.echo(
                            "❌ Guardian daemon process does not appear to be running."
                        )
                except Exception as ps_error:
                    typer.echo(f"Error checking process status: {str(ps_error)}")
        except subprocess.TimeoutExpired:
            typer.echo("Command timed out while checking daemon status.")
        except Exception as e:
            typer.echo(f"Error checking daemon status: {str(e)}")

    @app.command(name="logs")
    def daemon_logs(
        lines: int = typer.Option(
            20, "--lines", "-n", help="Number of log lines to show"
        )
    ):
        """Show recent logs from the guardian daemon."""
        typer.echo(f"Showing last {lines} lines of guardian daemon logs...\n")

        try:
            # Try different approaches for journalctl
            cmds = [
                [
                    "journalctl",
                    "-u",
                    "guardian_daemon.service",
                    "-n",
                    str(lines),
                ],  # Try without sudo first
                [
                    "sudo",
                    "-n",
                    "journalctl",
                    "-u",
                    "guardian_daemon.service",
                    "-n",
                    str(lines),
                ],  # sudo with non-interactive
                [
                    "sudo",
                    "journalctl",
                    "-u",
                    "guardian_daemon.service",
                    "-n",
                    str(lines),
                ],  # regular sudo (may prompt)
            ]

            success = False
            for cmd in cmds:
                try:
                    typer.echo(f"Running: {' '.join(cmd)}")
                    result = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=10
                    )
                    if result.returncode == 0 or result.stdout.strip():
                        typer.echo(result.stdout)
                        success = True
                        break
                    else:
                        typer.echo(
                            f"Command returned no output or error: {result.stderr}"
                        )
                except Exception as cmd_error:
                    typer.echo(f"Command failed: {cmd_error}")
                    continue

            if not success:
                # As a last resort, check syslog for guardian daemon mentions
                typer.echo("Trying to find guardian daemon logs in system logs...")
                try:
                    syslog_cmd = [
                        "grep",
                        "guardian_daemon",
                        "/var/log/syslog",
                        "|",
                        "tail",
                        f"-{lines}",
                    ]
                    syslog_result = subprocess.run(
                        " ".join(syslog_cmd), shell=True, capture_output=True, text=True
                    )
                    if syslog_result.stdout.strip():
                        typer.echo("Found guardian daemon mentions in syslog:")
                        typer.echo(syslog_result.stdout)
                    else:
                        typer.echo("No guardian daemon mentions found in syslog.")
                except Exception as syslog_error:
                    typer.echo(f"Error checking syslog: {str(syslog_error)}")
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
            # Try different approaches
            cmds = [
                [
                    "sudo",
                    "-n",
                    "systemctl",
                    "restart",
                    "guardian_daemon.service",
                ],  # Try non-interactive sudo first
                [
                    "sudo",
                    "systemctl",
                    "restart",
                    "guardian_daemon.service",
                ],  # Then interactive sudo
                [
                    "pkill",
                    "-f",
                    "guardian_daemon",
                    "&&",
                    "sleep",
                    "1",
                    "&&",
                    "sudo",
                    "systemctl",
                    "start",
                    "guardian_daemon.service",
                ],  # Last resort
            ]

            success = False
            for idx, cmd in enumerate(cmds):
                try:
                    if idx < 2:  # Standard commands
                        typer.echo(f"Running: {' '.join(cmd)}")
                        result = subprocess.run(
                            cmd, capture_output=True, text=True, timeout=10
                        )
                        if result.returncode == 0:
                            success = True
                            typer.echo("✅ Guardian daemon restart command succeeded!")
                            break
                        else:
                            typer.echo(f"Command failed: {result.stderr}")
                    else:  # Last resort with shell command
                        typer.echo("Attempting restart via process kill...")
                        shell_cmd = " ".join(cmd)
                        result = subprocess.run(
                            shell_cmd,
                            shell=True,
                            capture_output=True,
                            text=True,
                            timeout=10,
                        )
                        if result.returncode == 0:
                            success = True
                            typer.echo("✅ Guardian daemon process restarted!")
                            break
                except Exception as cmd_error:
                    typer.echo(f"Command failed: {cmd_error}")
                    continue

            if success:
                typer.echo("\nChecking daemon status after restart...")
                try:
                    # Wait a moment for the service to fully start
                    import time

                    time.sleep(2)

                    status_cmd = ["systemctl", "status", "guardian_daemon.service"]
                    status_result = subprocess.run(
                        status_cmd, capture_output=True, text=True, timeout=5
                    )
                    typer.echo(status_result.stdout)

                    # Also check for the socket
                    if os.path.exists(IPC_SOCKET):
                        typer.echo(f"✅ Guardian daemon socket {IPC_SOCKET} exists")
                    else:
                        typer.echo(
                            f"❌ Guardian daemon socket {IPC_SOCKET} does not exist"
                        )
                except Exception as status_error:
                    typer.echo(
                        f"Error checking status after restart: {str(status_error)}"
                    )
            else:
                typer.echo(
                    "❌ Failed to restart daemon. You may not have the necessary permissions."
                )
                typer.echo(
                    "Try running with sudo privileges or contact system administrator."
                )
        except subprocess.TimeoutExpired:
            typer.echo("Command timed out while restarting daemon.")
        except Exception as e:
            typer.echo(f"Error restarting daemon: {str(e)}")

    @app.command(name="config-check")
    def config_check():
        """Check the guardian daemon configuration."""
        import yaml

        typer.echo("Checking Guardian daemon configuration...\n")

        # Configuration paths
        default_config_path = "/usr/local/guardian/guardian_daemon/default-config.yaml"
        system_config_path = "/etc/guardian/daemon/config.yaml"

        try:
            # Check if config files exist
            default_exists = os.path.exists(default_config_path)
            system_exists = os.path.exists(system_config_path)

            typer.echo(f"Default config file: {default_config_path}")
            typer.echo(f"  Exists: {'✅ Yes' if default_exists else '❌ No'}")

            typer.echo(f"System config file: {system_config_path}")
            typer.echo(f"  Exists: {'✅ Yes' if system_exists else '❌ No'}")

            # Check config file contents if they exist
            if default_exists:
                try:
                    # Try to read file permissions
                    default_stats = os.stat(default_config_path)
                    default_perms = oct(default_stats.st_mode)[-3:]
                    typer.echo(f"  Permissions: {default_perms}")

                    # Try to parse YAML
                    try:
                        with open(default_config_path, "r") as f:
                            default_config = yaml.safe_load(f)
                        typer.echo("  YAML format: ✅ Valid")
                        typer.echo(
                            f"  Top-level keys: {', '.join(default_config.keys())}"
                        )
                    except Exception as yaml_err:
                        typer.echo(f"  YAML format: ❌ Invalid - {str(yaml_err)}")
                except Exception as stat_err:
                    typer.echo(f"  Error accessing file: {str(stat_err)}")

            if system_exists:
                try:
                    # Try to read file permissions
                    system_stats = os.stat(system_config_path)
                    system_perms = oct(system_stats.st_mode)[-3:]
                    typer.echo(f"  Permissions: {system_perms}")

                    # Try to parse YAML
                    try:
                        with open(system_config_path, "r") as f:
                            system_config = yaml.safe_load(f)
                        typer.echo("  YAML format: ✅ Valid")
                        typer.echo(
                            f"  Top-level keys: {', '.join(system_config.keys())}"
                        )

                        # Check for users section
                        if "users" in system_config:
                            users = system_config["users"]
                            typer.echo(
                                f"  User entries: {len(users)} ({'default' in users and 'default user present' or 'no default user'})"
                            )
                    except Exception as yaml_err:
                        typer.echo(f"  YAML format: ❌ Invalid - {str(yaml_err)}")
                except Exception as stat_err:
                    typer.echo(f"  Error accessing file: {str(stat_err)}")

            # Check database
            db_path = "/var/lib/guardian/guardian.sqlite"
            db_exists = os.path.exists(db_path)
            typer.echo(f"\nDatabase file: {db_path}")
            typer.echo(f"  Exists: {'✅ Yes' if db_exists else '❌ No'}")

            if db_exists:
                try:
                    db_stats = os.stat(db_path)
                    db_perms = oct(db_stats.st_mode)[-3:]
                    db_size = db_stats.st_size / 1024  # Size in KB
                    typer.echo(f"  Permissions: {db_perms}")
                    typer.echo(f"  Size: {db_size:.2f} KB")
                    typer.echo(
                        f"  Last modified: {datetime.fromtimestamp(db_stats.st_mtime)}"
                    )
                except Exception as db_err:
                    typer.echo(f"  Error accessing database: {str(db_err)}")

        except Exception as e:
            typer.echo(f"Error checking configuration: {str(e)}")


# Register commands at module load time
register_dynamic_commands()

if __name__ == "__main__":
    app()
