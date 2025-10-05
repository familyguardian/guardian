# CLI tool for guardianctl (Typer)

"""
CLI-Tool für guardianctl (Typer).
"""
import socket

import typer

app = typer.Typer()

IPC_SOCKET = "/run/guardian-daemon.sock"


def ipc_call(command, arg=None):
    """
    Send an IPC command to the daemon and return the response.
    """
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
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


# Static commands
@app.command()
def setup_user(username: str):
    """Set up a new user with the guardian system."""
    result = ipc_call("setup-user", username)
    typer.echo(result)


@app.command()
def remove_user(username: str):
    """Remove a user from the guardian system."""
    result = ipc_call("remove-user", username)
    typer.echo(result)


@app.command()
def show_users():
    """Show all users in the guardian system."""
    result = ipc_call("show-users")
    typer.echo(result)


@app.command()
def show_policy():
    """Show the current policy."""
    result = ipc_call("show-policy")
    typer.echo(result)


if __name__ == "__main__":
    app()
