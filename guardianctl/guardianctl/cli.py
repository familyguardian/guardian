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
        # If we couldn't get commands from the daemon, register some basic ones as fallback
        typer.echo(
            "Could not fetch commands from daemon, using basic commands", err=True
        )
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
            from contextlib import redirect_stdout
            import io

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
    Register basic commands as fallback when we can't query the daemon.
    These commands correspond to the ones we know exist in the daemon.
    """

    @app.command()
    def setup_user(username: str):
        """Set up a new user with the guardian system."""
        result = ipc_call("setup-user", username)
        try:
            # Try to pretty-print JSON responses
            parsed = json.loads(result)
            typer.echo(json.dumps(parsed, indent=2))
        except (json.JSONDecodeError, ValueError):
            # If not JSON, just print as-is
            typer.echo(result)

    @app.command()
    def remove_user(username: str):
        """Remove a user from the guardian system."""
        result = ipc_call("remove-user", username)
        try:
            parsed = json.loads(result)
            typer.echo(json.dumps(parsed, indent=2))
        except (json.JSONDecodeError, ValueError):
            typer.echo(result)

    @app.command(name="show-users")
    def show_users():
        """Show all users in the guardian system."""
        result = ipc_call("list_kids")
        try:
            parsed = json.loads(result)
            if "kids" in parsed:
                typer.echo("Users in the guardian system:")
                for user in parsed["kids"]:
                    typer.echo(f"  - {user}")
            else:
                typer.echo(json.dumps(parsed, indent=2))
        except (json.JSONDecodeError, ValueError):
            typer.echo(result)

    @app.command(name="show-policy")
    def show_policy():
        """Show the current policy."""
        # Try to get policy information using available commands
        try:
            # First get the list of users
            users_result = ipc_call("list_kids")
            users_data = json.loads(users_result)

            if "kids" in users_data and users_data["kids"]:
                typer.echo("Policy information:")
                for user in users_data["kids"]:
                    # Get curfew for this user
                    curfew_result = ipc_call("get_curfew", user)
                    try:
                        curfew_data = json.loads(curfew_result)
                        typer.echo(f"\nUser: {user}")
                        typer.echo(f"  Curfew: {curfew_data.get('curfew', 'Not set')}")

                        # Get quota if available
                        quota_result = ipc_call("get_quota", user)
                        try:
                            quota_data = json.loads(quota_result)
                            if "error" not in quota_data:
                                typer.echo(
                                    f"  Daily limit: {quota_data.get('limit', 'Not set')} minutes"
                                )
                                typer.echo(
                                    f"  Used today: {quota_data.get('used', 0)} minutes"
                                )
                                typer.echo(
                                    f"  Remaining: {quota_data.get('remaining', 0)} minutes"
                                )
                        except (json.JSONDecodeError, ValueError, KeyError):
                            # Log error but don't display to keep the output clean
                            pass
                    except (json.JSONDecodeError, ValueError, KeyError):
                        # Log error but don't display to keep the output clean
                        pass
            else:
                typer.echo("No users found in the system.")
        except Exception as e:
            typer.echo(f"Error getting policy information: {e}")
            # Fallback to basic curfew info
            result = ipc_call("get_curfew", "_")
            try:
                parsed = json.loads(result)
                typer.echo(json.dumps(parsed, indent=2))
            except (json.JSONDecodeError, ValueError) as e:
                typer.echo(result)


# Register commands at module load time
register_dynamic_commands()

if __name__ == "__main__":
    app()
