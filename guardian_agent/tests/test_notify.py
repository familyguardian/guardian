"""Unit tests for the Guardian agent's notification mapping.

Validates that NotifyUser translates each category into the correct notify-send
argv (icon / urgency / expire), and handles a missing notify-send binary.

dbus-next's @method wrapper returns None when a service method is called directly
(it is meant to be invoked over the bus), so we call the underlying coroutine via
``__wrapped__`` to exercise the logic in isolation.
"""

import asyncio

import pytest

from guardian_agent.__main__ import GuardianAgentInterface

# The raw coroutines behind the dbus-next @method wrappers.
_notify = GuardianAgentInterface.NotifyUser.__wrapped__
_get_username = GuardianAgentInterface.GetUsername.__wrapped__


@pytest.fixture
def captured_exec(monkeypatch):
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)

        class _Proc:
            returncode = 0

            async def communicate(self):
                return (b"", b"")

        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return calls


async def test_get_username():
    agent = GuardianAgentInterface("kid1")
    assert await _get_username(agent) == "kid1"


@pytest.mark.parametrize(
    "category,icon,urgency,expire",
    [
        ("info", "dialog-information", "low", "10000"),
        ("warning", "dialog-warning", "normal", "20000"),
        ("critical", "dialog-error", "critical", "60000"),
    ],
)
async def test_notify_category_argv(captured_exec, category, icon, urgency, expire):
    agent = GuardianAgentInterface("kid1")
    await _notify(agent, "hello", category)

    assert len(captured_exec) == 1
    argv = list(captured_exec[0])
    assert argv[0] == "notify-send"
    # -a Guardian -i <icon> -u <urgency> -t <expire> <message>
    assert argv[argv.index("-a") + 1] == "Guardian"
    assert argv[argv.index("-i") + 1] == icon
    assert argv[argv.index("-u") + 1] == urgency
    assert argv[argv.index("-t") + 1] == expire
    assert argv[-1] == "hello"


async def test_unknown_category_falls_back_to_info(captured_exec):
    agent = GuardianAgentInterface("kid1")
    await _notify(agent, "hi", "bogus")
    argv = list(captured_exec[0])
    assert argv[argv.index("-i") + 1] == "dialog-information"


async def test_missing_notify_send_is_handled(monkeypatch):
    async def boom(*args, **kwargs):
        raise FileNotFoundError("notify-send")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    agent = GuardianAgentInterface("kid1")
    # Should swallow the error and return "" rather than raising.
    assert await _notify(agent, "hi", "info") == ""
