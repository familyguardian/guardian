"""
Extended unit tests for the sessions module to improve code coverage.
"""

import time

import pytest

from guardian_daemon.policy import Policy
from guardian_daemon.sessions import SessionTracker
from guardian_daemon.storage import Storage


@pytest.mark.asyncio
async def test_get_total_time_unlimited(test_config, mock_dbus, mocker):
    """Test get_total_time for user not in policy (unlimited)."""
    config, config_path = test_config
    mock_bus, mock_logind = mock_dbus

    policy = Policy(config_path)
    storage = Storage(config["db_path"])
    mock_user_manager = mocker.MagicMock()

    session_tracker = SessionTracker(policy, storage, mock_user_manager)

    # Test user not in policy
    total_time = await session_tracker.get_total_time("nonexistent_user")
    assert total_time == float("inf")


@pytest.mark.asyncio
async def test_get_total_time_with_quota(test_config, mock_dbus, mocker):
    """Test get_total_time for user with quota."""
    config, config_path = test_config
    mock_bus, mock_logind = mock_dbus

    policy = Policy(config_path)
    storage = Storage(config["db_path"])
    mock_user_manager = mocker.MagicMock()

    session_tracker = SessionTracker(policy, storage, mock_user_manager)

    # Test user with quota (60 minutes per day)
    total_time = await session_tracker.get_total_time("test_quota_only")
    assert total_time == 60.0


@pytest.mark.asyncio
async def test_get_remaining_time_unlimited(test_config, mock_dbus, mocker):
    """Test get_remaining_time for user with unlimited time."""
    config, config_path = test_config
    mock_bus, mock_logind = mock_dbus

    policy = Policy(config_path)
    storage = Storage(config["db_path"])
    mock_user_manager = mocker.MagicMock()

    session_tracker = SessionTracker(policy, storage, mock_user_manager)

    # Test user not in policy (unlimited)
    remaining = await session_tracker.get_remaining_time("nonexistent_user")
    assert remaining == float("inf")


@pytest.mark.asyncio
async def test_get_remaining_time_with_active_sessions(test_config, mock_dbus, mocker):
    """Test get_remaining_time with active sessions."""
    config, config_path = test_config
    mock_bus, mock_logind = mock_dbus

    policy = Policy(config_path)
    storage = Storage(config["db_path"])
    mock_user_manager = mocker.MagicMock()

    session_tracker = SessionTracker(policy, storage, mock_user_manager)

    # Add an active session
    username = "test_quota_only"
    session_id = "test_session_1"
    now = time.time()

    async with session_tracker.session_lock:
        session_tracker.active_sessions[session_id] = {
            "username": username,
            "start_time": now - 300,  # Started 5 minutes ago
            "desktop": "gnome",
            "service": "user",
        }

    # Get remaining time (should be 60 - 5 = 55 minutes)
    remaining = await session_tracker.get_remaining_time(username)
    assert 54.0 < remaining < 56.0  # Allow small variance


@pytest.mark.asyncio
async def test_get_remaining_time_with_locked_sessions(test_config, mock_dbus, mocker):
    """Test get_remaining_time properly excludes locked time."""
    config, config_path = test_config
    mock_bus, mock_logind = mock_dbus

    policy = Policy(config_path)
    storage = Storage(config["db_path"])
    mock_user_manager = mocker.MagicMock()

    session_tracker = SessionTracker(policy, storage, mock_user_manager)

    # Add an active session
    username = "test_quota_only"
    session_id = "test_session_1"
    now = time.time()

    async with session_tracker.session_lock:
        session_tracker.active_sessions[session_id] = {
            "username": username,
            "start_time": now - 600,  # Started 10 minutes ago
            "desktop": "gnome",
            "service": "user",
        }
        # Add lock period of 5 minutes
        session_tracker.session_locks[session_id] = [
            (now - 300, now)  # Locked for last 5 minutes
        ]

    # Get remaining time (should be 60 - 5 = 55 minutes, not 60 - 10 = 50)
    remaining = await session_tracker.get_remaining_time(username)
    assert 54.0 < remaining < 56.0  # Allow small variance


@pytest.mark.asyncio
async def test_receive_lock_event_lock(test_config, mock_dbus, mocker):
    """Test receive_lock_event when locking."""
    config, config_path = test_config
    mock_bus, mock_logind = mock_dbus

    policy = Policy(config_path)
    storage = Storage(config["db_path"])
    mock_user_manager = mocker.MagicMock()

    session_tracker = SessionTracker(policy, storage, mock_user_manager)

    session_id = "test_session"
    username = "testuser"
    now = time.time()

    # Add active session
    async with session_tracker.session_lock:
        session_tracker.active_sessions[session_id] = {
            "username": username,
            "start_time": now - 100,
            "desktop": "gnome",
        }

    # Lock the session
    await session_tracker.receive_lock_event(session_id, username, True, now)

    # Verify lock was recorded
    async with session_tracker.session_lock:
        assert session_id in session_tracker.session_locks
        assert len(session_tracker.session_locks[session_id]) == 1
        assert session_tracker.session_locks[session_id][0][0] == now
        assert session_tracker.session_locks[session_id][0][1] is None


@pytest.mark.asyncio
async def test_receive_lock_event_unlock(test_config, mock_dbus, mocker):
    """Test receive_lock_event when unlocking."""
    config, config_path = test_config
    mock_bus, mock_logind = mock_dbus

    policy = Policy(config_path)
    storage = Storage(config["db_path"])
    mock_user_manager = mocker.MagicMock()

    session_tracker = SessionTracker(policy, storage, mock_user_manager)

    session_id = "test_session"
    username = "testuser"
    now = time.time()

    # Add active session and lock
    async with session_tracker.session_lock:
        session_tracker.active_sessions[session_id] = {
            "username": username,
            "start_time": now - 200,
            "desktop": "gnome",
        }
        session_tracker.session_locks[session_id] = [(now - 100, None)]

    # Unlock the session
    await session_tracker.receive_lock_event(session_id, username, False, now)

    # Verify lock was removed (it gets popped after being closed)
    async with session_tracker.session_lock:
        assert session_id in session_tracker.session_locks
        assert (
            len(session_tracker.session_locks[session_id]) == 0
        )  # Lock entry was removed
        # Verify session start_time was adjusted to exclude locked duration
        assert session_tracker.active_sessions[session_id]["start_time"] == now - 100


@pytest.mark.asyncio
async def test_get_user_sessions(test_config, mock_dbus, mocker):
    """Test get_user_sessions returns correct sessions."""
    config, config_path = test_config
    mock_bus, mock_logind = mock_dbus

    policy = Policy(config_path)
    storage = Storage(config["db_path"])
    mock_user_manager = mocker.MagicMock()

    session_tracker = SessionTracker(policy, storage, mock_user_manager)

    username = "testuser"
    now = time.time()

    # Add sessions for different users
    async with session_tracker.session_lock:
        session_tracker.active_sessions["session1"] = {
            "username": username,
            "start_time": now - 100,
            "service": "user",
            "desktop": "gnome",
        }
        session_tracker.active_sessions["session2"] = {
            "username": "otheruser",
            "start_time": now - 50,
            "service": "user",
            "desktop": "kde",
        }
        session_tracker.active_sessions["session3"] = {
            "username": username,
            "start_time": now - 25,
            "service": "user",
            "desktop": "xfce",
        }

    # Get sessions for testuser
    sessions = session_tracker.get_user_sessions(username)

    assert len(sessions) == 2
    assert all(s["username"] == username for s in sessions)


@pytest.mark.asyncio
async def test_get_agent_paths_for_user(test_config, mock_dbus, mocker):
    """Test get_agent_paths_for_user returns correct paths."""
    config, config_path = test_config
    mock_bus, mock_logind = mock_dbus

    policy = Policy(config_path)
    storage = Storage(config["db_path"])
    mock_user_manager = mocker.MagicMock()

    session_tracker = SessionTracker(policy, storage, mock_user_manager)

    username = "testuser"

    # Add sessions with agent paths
    async with session_tracker.session_lock:
        session_tracker.active_sessions["session1"] = {
            "username": username,
            "agent_path": "/org/guardian/Agent1",
        }
        session_tracker.active_sessions["session2"] = {
            "username": "otheruser",
            "agent_path": "/org/guardian/Agent2",
        }
        session_tracker.active_sessions["session3"] = {
            "username": username,
            "agent_path": "/org/guardian/Agent3",
        }

    # Get agent paths for testuser
    paths = session_tracker.get_agent_paths_for_user(username)

    assert len(paths) == 2
    assert "/org/guardian/Agent1" in paths
    assert "/org/guardian/Agent3" in paths
    assert "/org/guardian/Agent2" not in paths


@pytest.mark.asyncio
async def test_get_agent_paths_for_user_fallback(test_config, mock_dbus, mocker):
    """Test get_agent_paths_for_user returns fallback paths when no sessions."""
    config, config_path = test_config
    mock_bus, mock_logind = mock_dbus

    policy = Policy(config_path)
    storage = Storage(config["db_path"])
    mock_user_manager = mocker.MagicMock()

    session_tracker = SessionTracker(policy, storage, mock_user_manager)

    # Get agent paths for user with no sessions
    paths = session_tracker.get_agent_paths_for_user("nonexistent")

    # Should return default fallback paths
    assert len(paths) > 0
    assert "/org/guardian/Agent" in paths


@pytest.mark.asyncio
async def test_get_agent_names_for_user(test_config, mock_dbus, mocker):
    """Test get_agent_names_for_user retrieves agent names."""
    config, config_path = test_config
    mock_bus, mock_logind = mock_dbus

    policy = Policy(config_path)
    storage = Storage(config["db_path"])
    mock_user_manager = mocker.MagicMock()

    session_tracker = SessionTracker(policy, storage, mock_user_manager)

    username = "testuser"
    session_tracker.agent_name_map[username] = [
        "org.guardian.Agent1",
        "org.guardian.Agent2",
    ]

    names = session_tracker.get_agent_names_for_user(username)

    assert len(names) == 2
    assert "org.guardian.Agent1" in names
    assert "org.guardian.Agent2" in names


@pytest.mark.asyncio
async def test_get_agent_names_for_user_empty(test_config, mock_dbus, mocker):
    """Test get_agent_names_for_user returns empty list for unknown user."""
    config, config_path = test_config
    mock_bus, mock_logind = mock_dbus

    policy = Policy(config_path)
    storage = Storage(config["db_path"])
    mock_user_manager = mocker.MagicMock()

    session_tracker = SessionTracker(policy, storage, mock_user_manager)

    names = session_tracker.get_agent_names_for_user("nonexistent")

    assert names == []
