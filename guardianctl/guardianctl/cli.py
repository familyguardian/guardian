# CLI tool for guardianctl (Typer)

"""
CLI-Tool für guardianctl (Typer).
Dynamisch generiert basierend auf den verfügbaren IPC-Kommandos des Daemons.
"""
import json
import socket

import typer

app = typer.Typer()

IPC_SOCKET = "/run/guardian-daemon.sock"


def ipc_call(command, **kwargs):
    """
    Send an IPC command to the daemon and return the response.
    """
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(IPC_SOCKET)

        # The new protocol expects a command string, not a JSON object.
        # The command format is "command_name" or "command_name argument".
        # The dynamic command generation seems to pass arguments in kwargs.
        # We'll assume for commands with one parameter, it's in the kwargs.
        arg = next(iter(kwargs.values())) if kwargs else None
        if arg:
            message = f"{command} {arg}"
        else:
            message = command

        # Send message length then message
        message_data = message.encode()
        s.sendall(len(message_data).to_bytes(4, "big"))
        s.sendall(message_data)

        # Read response length then response
        len_data = s.recv(4)
        if not len_data:
            return json.dumps({"error": "No response from daemon"})
        msg_len = int.from_bytes(len_data, "big")

        resp = s.recv(msg_len)
        return resp.decode()


def get_ipc_commands():
    """
    Retrieve available IPC commands and their parameters from the daemon.
    """
    resp = ipc_call("describe_commands")
    return json.loads(resp)


# Dynamisch generierte Typer-Kommandos
for cmd, meta in get_ipc_commands().items():

    def make_cmd(cmd_name, params):
        """
        Create a Typer command for the given IPC command name and parameters.
        """

        def _cmd(**kwargs):
            """
            Execute the IPC command and print the result.
            """
            result = ipc_call(cmd_name, **kwargs)
            typer.echo(result)

        return _cmd

    # Typer benötigt die Parameter als Funktionsargumente
    param_defs = {p: typer.Option(None, help=f"Parameter {p}") for p in meta["params"]}
    app.command(name=cmd)(
        typer.main.get_command(make_cmd(cmd, meta["params"]), param_defs)
    )

if __name__ == "__main__":
    app()
