"""L2: SessionTracker against a real D-Bus broker + mock logind.

These drive guardian's real ``SessionTracker.run()`` code path - ListSessions
parsing, introspection-driven property getters, the (uid, path) ``User`` struct
and SessionNew/SessionRemoved signal handling - against a real bus. This is the
layer that catches "passes the MagicMock tests but breaks against real logind"
bugs.
"""

import asyncio

import pytest

pytestmark = pytest.mark.dbus


async def test_initial_scan_tracks_existing_kid_session(
    make_tracker, mock_logind, running_tracker
):
    # A child session already exists when the daemon starts (no signal emitted -
    # run() must discover it via ListSessions).
    mock_logind.add_session(
        "c1", 1000, "kid1", desktop="KDE", service="sddm", emit=False
    )

    tracker = make_tracker(users={"kid1": {}})
    await running_tracker(tracker)

    assert len(tracker.active_sessions) == 1
    entry = next(iter(tracker.active_sessions.values()))
    assert entry["username"] == "kid1"
    # Proves the real introspection-driven property reads worked: Desktop is a
    # field real logind exposes (and that a naive MagicMock would leave as None).
    assert entry["desktop"] == "KDE"
    assert entry["service"] == "sddm"


async def test_session_new_signal_is_tracked(
    make_tracker, mock_logind, running_tracker
):
    tracker = make_tracker(users={"kid1": {}})

    # The SessionNew code path resolves the username from the uid via _get_username
    # (pwd lookup on the host); pin it so the test does not depend on host users.
    async def fake_get_username(uid):
        return "kid1"

    tracker._get_username = fake_get_username

    await running_tracker(tracker)
    assert tracker.active_sessions == {}

    mock_logind.add_session("c2", 1000, "kid1", emit=True)
    await asyncio.sleep(0.4)

    assert len(tracker.active_sessions) == 1
    entry = next(iter(tracker.active_sessions.values()))
    assert entry["username"] == "kid1"


async def test_session_removed_signal_ends_session(
    make_tracker, mock_logind, running_tracker
):
    mock_logind.add_session("c3", 1000, "kid1", emit=False)
    tracker = make_tracker(users={"kid1": {}})
    await running_tracker(tracker)
    assert len(tracker.active_sessions) == 1

    mock_logind.remove_session("c3", emit=True)
    await asyncio.sleep(0.4)

    assert tracker.active_sessions == {}


async def test_non_managed_user_session_ignored(
    make_tracker, mock_logind, running_tracker
):
    mock_logind.add_session("c4", 1001, "alice", emit=False)
    tracker = make_tracker(users={"kid1": {}})
    await running_tracker(tracker)
    assert tracker.active_sessions == {}


async def test_systemd_user_session_ignored(make_tracker, mock_logind, running_tracker):
    # A real kid, but a non-interactive systemd-user session must be filtered out.
    mock_logind.add_session("c5", 1000, "kid1", service="systemd-user", emit=False)
    tracker = make_tracker(users={"kid1": {}})
    await running_tracker(tracker)
    assert tracker.active_sessions == {}


async def test_background_session_ignored(make_tracker, mock_logind, running_tracker):
    mock_logind.add_session("c6", 1000, "kid1", klass="background", emit=False)
    tracker = make_tracker(users={"kid1": {}})
    await running_tracker(tracker)
    assert tracker.active_sessions == {}
