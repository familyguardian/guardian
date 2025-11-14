"""
Additional concise tests for sessions.py to push coverage over 50%.
Focus on simple, testable methods without complex mocking requirements.
"""

import time
from unittest.mock import MagicMock

import pytest

from guardian_daemon.policy import Policy
from guardian_daemon.sessions import GuardianDaemonInterface, SessionTracker
from guardian_daemon.storage import Storage
from guardian_daemon.user_manager import UserManager


@pytest.fixture
def session_tracker(test_config):
    """Create a SessionTracker with test configuration."""
    config, config_path = test_config
    policy = Policy(config_path)
    storage = Storage(config["db_path"])
    user_manager = UserManager(policy)
    return SessionTracker(policy, storage, user_manager)


# ============================================================================
# Simple method tests
# ============================================================================


def test_get_agent_names_for_user_empty(session_tracker):
    """Test get_agent_names_for_user returns empty list for unknown user."""
    names = session_tracker.get_agent_names_for_user("unknownuser")
    assert names == []


def test_get_agent_names_for_user_set_conversion(session_tracker):
    """Test get_agent_names_for_user converts set to list."""
    # Store as set
    session_tracker.agent_name_map["testuser"] = {"name1", "name2"}

    names = session_tracker.get_agent_names_for_user("testuser")

    assert isinstance(names, list)
    assert len(names) == 2
    assert "name1" in names
    assert "name2" in names


def test_get_agent_names_for_user_list_passthrough(session_tracker):
    """Test get_agent_names_for_user returns list as-is."""
    # Store as list
    session_tracker.agent_name_map["testuser"] = ["name1", "name2"]

    names = session_tracker.get_agent_names_for_user("testuser")

    assert names == ["name1", "name2"]


def test_get_user_sessions_empty(session_tracker):
    """Test get_user_sessions returns empty list for user with no sessions."""
    sessions = session_tracker.get_user_sessions("unknownuser")

    assert sessions == []


def test_get_user_sessions_with_active_sessions(session_tracker):
    """Test get_user_sessions returns active sessions for user."""
    # Add some active sessions
    session_tracker.active_sessions = {
        "session1": {
            "username": "testuser",
            "uid": 1000,
            "start_time": time.time(),
            "desktop": "gnome",
        },
        "session2": {
            "username": "otheruser",
            "uid": 1001,
            "start_time": time.time(),
            "desktop": "kde",
        },
        "session3": {
            "username": "testuser",
            "uid": 1000,
            "start_time": time.time(),
            "desktop": "xfce",
        },
    }

    sessions = session_tracker.get_user_sessions("testuser")

    assert len(sessions) == 2
    assert all(s["username"] == "testuser" for s in sessions)
    session_ids = [s["session_id"] for s in sessions]
    assert "session1" in session_ids
    assert "session3" in session_ids
    assert "session2" not in session_ids


def test_get_agent_paths_for_user_from_sessions(session_tracker):
    """Test get_agent_paths_for_user extracts paths from active sessions."""
    # Add sessions with agent_path
    session_tracker.active_sessions = {
        "session1": {
            "username": "testuser",
            "agent_path": "/org/guardian/Agent1",
        },
        "session2": {
            "username": "testuser",
            "agent_path": "/org/guardian/Agent2",
        },
        "session3": {
            "username": "otheruser",
            "agent_path": "/org/guardian/Agent3",
        },
    }

    paths = session_tracker.get_agent_paths_for_user("testuser")

    assert len(paths) == 2
    assert "/org/guardian/Agent1" in paths
    assert "/org/guardian/Agent2" in paths
    assert "/org/guardian/Agent3" not in paths


def test_get_agent_paths_for_user_fallback(session_tracker):
    """Test get_agent_paths_for_user returns fallback paths when no sessions."""
    paths = session_tracker.get_agent_paths_for_user("testuser")

    # Should return default fallback paths
    assert len(paths) > 0
    assert "/org/guardian/Agent" in paths


# ============================================================================
# GuardianDaemonInterface tests
# ============================================================================


@pytest.mark.asyncio
async def test_daemon_interface_initialization():
    """Test GuardianDaemonInterface initializes correctly."""
    mock_tracker = MagicMock()
    interface = GuardianDaemonInterface(mock_tracker)

    assert interface.session_tracker == mock_tracker


@pytest.mark.asyncio
async def test_daemon_interface_lock_event_known_session(session_tracker):
    """Test LockEvent method handles known session by calling receive_lock_event."""
    # Add an active session
    session_id = "test_session"
    username = "test_full_settings"  # User in test_config
    timestamp = time.time()
    session_tracker.active_sessions[session_id] = {
        "username": username,
        "uid": 1000,
        "start_time": timestamp,
    }
    session_tracker.session_locks[session_id] = []

    # Call receive_lock_event directly (this is what LockEvent calls internally)
    await session_tracker.receive_lock_event(session_id, username, True, timestamp)

    # Should have recorded the lock
    assert len(session_tracker.session_locks[session_id]) == 1
    assert session_tracker.session_locks[session_id][0][0] == timestamp


@pytest.mark.asyncio
async def test_daemon_interface_lock_event_unknown_session(session_tracker):
    """Test receive_lock_event handles unknown session gracefully."""
    # Call receive_lock_event for unknown session - should log warning but not crash
    await session_tracker.receive_lock_event(
        "unknown_session", "testuser", True, time.time()
    )

    # Should not have created entry for unknown session
    assert "unknown_session" not in session_tracker.session_locks


# ============================================================================
# Session lock handling tests
# ============================================================================


def test_session_locks_initialization(session_tracker):
    """Test that session_locks is initialized as empty dict."""
    assert isinstance(session_tracker.session_locks, dict)
    assert len(session_tracker.session_locks) == 0


def test_agent_name_map_initialization(session_tracker):
    """Test that agent_name_map is initialized as empty dict."""
    assert isinstance(session_tracker.agent_name_map, dict)
    assert len(session_tracker.agent_name_map) == 0


def test_pause_user_time(session_tracker):
    """Test pause_user_time creates user_locks attribute and stores timestamp."""
    timestamp = time.time()

    session_tracker.pause_user_time("testuser", timestamp)

    assert hasattr(session_tracker, "user_locks")
    assert "testuser" in session_tracker.user_locks
    assert session_tracker.user_locks["testuser"] == timestamp


def test_pause_user_time_multiple_users(session_tracker):
    """Test pause_user_time handles multiple users independently."""
    time1 = time.time()
    time2 = time1 + 100

    session_tracker.pause_user_time("user1", time1)
    session_tracker.pause_user_time("user2", time2)

    assert session_tracker.user_locks["user1"] == time1
    assert session_tracker.user_locks["user2"] == time2


# ============================================================================
# Additional coverage tests
# ============================================================================


@pytest.mark.asyncio
async def test_get_active_users(session_tracker):
    """Test get_active_users returns list of active usernames."""
    # Add some active sessions
    session_tracker.active_sessions = {
        "s1": {"username": "user1"},
        "s2": {"username": "user2"},
        "s3": {"username": "user1"},  # Duplicate
    }

    active = await session_tracker.get_active_users()

    assert "user1" in active
    assert "user2" in active
    assert len(active) == 3  # Should include duplicates


@pytest.mark.asyncio
async def test_get_total_time_unlimited(session_tracker):
    """Test get_total_time returns inf for unmonitored user."""
    total = await session_tracker.get_total_time("nonexistent_user")

    assert total == float("inf")


@pytest.mark.asyncio
async def test_get_total_time_with_quota(session_tracker):
    """Test get_total_time returns quota for monitored user."""
    # test_full_settings has daily quota of 120 minutes
    total = await session_tracker.get_total_time("test_full_settings")

    assert total == 120.0


@pytest.mark.asyncio
async def test_get_remaining_time_unlimited(session_tracker):
    """Test get_remaining_time returns inf for user without quota."""
    remaining = await session_tracker.get_remaining_time("nonexistent_user")

    assert remaining == float("inf")


@pytest.mark.asyncio
async def test_receive_lock_event_lock(session_tracker):
    """Test receive_lock_event records lock start."""
    session_id = "test_session"
    username = "testuser"
    timestamp = time.time()

    # Add active session
    session_tracker.active_sessions[session_id] = {
        "username": username,
        "start_time": timestamp - 100,
    }
    session_tracker.session_locks[session_id] = []

    # Lock the session
    await session_tracker.receive_lock_event(session_id, username, True, timestamp)

    # Should have one lock entry with start time
    assert len(session_tracker.session_locks[session_id]) == 1
    assert session_tracker.session_locks[session_id][0][0] == timestamp
    assert session_tracker.session_locks[session_id][0][1] is None  # End time not set


@pytest.mark.asyncio
async def test_receive_lock_event_unlock(session_tracker):
    """Test receive_lock_event records lock end."""
    session_id = "test_session"
    username = "testuser"
    lock_start = time.time()
    unlock_time = lock_start + 60

    # Add active session with existing lock
    session_tracker.active_sessions[session_id] = {
        "username": username,
        "start_time": lock_start - 100,
    }
    session_tracker.session_locks[session_id] = [(lock_start, None)]

    # Unlock the session
    await session_tracker.receive_lock_event(session_id, username, False, unlock_time)

    # Lock entry should be removed (as per code implementation)
    assert len(session_tracker.session_locks[session_id]) == 0


@pytest.mark.asyncio
async def test_handle_name_owner_changed(session_tracker):
    """Test _handle_name_owner_changed processes agent name changes."""
    # Test agent name appearing
    session_tracker._handle_name_owner_changed(
        "org.guardian.Agent.testuser.1234",
        "",  # old owner empty means new
        ":1.42",  # new owner
    )

    assert "testuser" in session_tracker.agent_name_map
    assert (
        "org.guardian.Agent.testuser.1234" in session_tracker.agent_name_map["testuser"]
    )

    # Test agent name disappearing
    session_tracker._handle_name_owner_changed(
        "org.guardian.Agent.testuser.1234",
        ":1.42",  # old owner
        "",  # new owner empty means removed
    )

    # Should be removed from the map
    assert "org.guardian.Agent.testuser.1234" not in session_tracker.agent_name_map.get(
        "testuser", set()
    )


def test_handle_name_owner_changed_invalid_format(session_tracker):
    """Test _handle_name_owner_changed handles invalid names gracefully."""
    # Name with too few parts should be handled gracefully
    session_tracker._handle_name_owner_changed(
        "org.guardian.Agent",
        "",
        ":1.42",  # Missing username and PID
    )

    # Should not crash, map should remain empty
    assert len(session_tracker.agent_name_map) == 0


def test_handle_name_owner_changed_non_agent_name(session_tracker):
    """Test _handle_name_owner_changed ignores non-agent names."""
    session_tracker._handle_name_owner_changed("org.freedesktop.DBus", "", ":1.42")

    # Should not add to agent map
    assert len(session_tracker.agent_name_map) == 0


@pytest.mark.asyncio
async def test_get_remaining_time_with_db_sessions(session_tracker):
    """
    Test get_remaining_time correctly calculates used time from database sessions.
    This is a regression test for the bug where wrong tuple indices were used.

    Bug: Code was using s[6] for duration, but actual tuple format is:
    (session_id, username, uid, start_time, end_time, duration, desktop, service)
    So duration is at index 5, not 6.
    """
    # Use a user from the test config who has a quota
    username = "test_full_settings"  # Has daily quota of 120 minutes    # Add some completed sessions to the database
    now = time.time()
    session_start = now - 3600  # 1 hour ago

    # Add a session with 30 minutes (1800 seconds) of usage
    await session_tracker.storage.add_session(
        session_id="test_session_1",
        username=username,
        uid=1000,
        start_time=session_start,
        end_time=session_start + 1800,
        duration_seconds=1800.0,  # 30 minutes
        desktop="plasma",
        service="sddm",
    )

    # Add another session with 45 minutes (2700 seconds) of usage
    await session_tracker.storage.add_session(
        session_id="test_session_2",
        username=username,
        uid=1000,
        start_time=session_start + 2000,
        end_time=session_start + 4700,
        duration_seconds=2700.0,  # 45 minutes
        desktop="gnome",
        service="gdm",
    )

    # User has 120 minutes daily quota in test config
    remaining = await session_tracker.get_remaining_time(username)

    # Should have used 75 minutes (30 + 45), so 45 minutes remaining
    # Allow small floating point tolerance
    expected_remaining = 120.0 - 75.0  # 45 minutes
    assert abs(remaining - expected_remaining) < 0.1, (
        f"Expected ~{expected_remaining} minutes remaining, got {remaining}. "
        f"This might indicate the tuple indices bug is present."
    )
