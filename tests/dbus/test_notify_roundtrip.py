"""L2: end-to-end notification delivery over a real bus.

Chain under test: Enforcer.notify_user -> (system bus) -> real
GuardianAgentInterface.NotifyUser -> real `notify-send` subprocess ->
(session bus) -> mock org.freedesktop.Notifications.

This is the direct regression test for "quota warnings never reach the child",
exercising the full daemon->agent->libnotify path that the MagicMock unit tests
cannot.
"""

import asyncio

import pytest
import pytest_asyncio
from dbus_next.aio import MessageBus
from dbus_next.constants import BusType

from guardian_agent.__main__ import GuardianAgentInterface
from guardian_daemon.enforcer import Enforcer

pytestmark = pytest.mark.dbus


@pytest_asyncio.fixture
async def agent_factory(dbus_broker):
    """Start real Guardian agents on the system bus; clean them up afterwards."""
    buses = []

    async def _start(username, pid=99999):
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        iface = GuardianAgentInterface(username)
        bus.export("/org/guardian/Agent", iface)
        name = f"org.guardian.Agent.{username}.pid{pid}"
        await bus.request_name(name)
        buses.append(bus)
        return name

    try:
        yield _start
    finally:
        for bus in buses:
            bus.disconnect()


async def test_notify_reaches_notification_daemon(
    make_tracker, mock_notifications, agent_factory
):
    agent_name = await agent_factory("kid1")

    tracker = make_tracker(users={"kid1": {}})
    # Make the daemon aware of the running agent (normally done by discovery).
    tracker.agent_name_map["kid1"] = {agent_name}

    enforcer = Enforcer(tracker.policy, tracker)
    await enforcer.notify_user("kid1", "Time is almost up!", "warning")
    await asyncio.sleep(0.3)

    assert len(mock_notifications.received) == 1
    note = mock_notifications.received[0]
    assert note["app_name"] == "Guardian"
    assert note["summary"] == "Time is almost up!"
    assert note["app_icon"] == "dialog-warning"
    # "warning" -> urgency "normal" -> libnotify byte 1, and -t 20000.
    assert note["hints"].get("urgency") == 1
    assert note["expire_timeout"] == 20000


async def test_notify_critical_category_mapping(
    make_tracker, mock_notifications, agent_factory
):
    agent_name = await agent_factory("kid1")
    tracker = make_tracker(users={"kid1": {}})
    tracker.agent_name_map["kid1"] = {agent_name}
    enforcer = Enforcer(tracker.policy, tracker)

    await enforcer.notify_user("kid1", "Logging you out now.", "critical")
    await asyncio.sleep(0.3)

    assert len(mock_notifications.received) == 1
    note = mock_notifications.received[0]
    assert note["app_icon"] == "dialog-error"
    assert note["hints"].get("urgency") == 2  # critical
    assert note["expire_timeout"] == 60000


async def test_notify_no_agent_delivers_nothing(make_tracker, mock_notifications):
    # No agent registered for the user -> nothing should be delivered.
    tracker = make_tracker(users={"kid1": {}})
    enforcer = Enforcer(tracker.policy, tracker)

    await enforcer.notify_user("kid1", "nobody home", "info")
    await asyncio.sleep(0.2)

    assert mock_notifications.received == []
