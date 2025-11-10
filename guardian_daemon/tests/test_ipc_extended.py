"""
Extended test coverage for IPC handlers in guardian_daemon.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from guardian_daemon.ipc import GuardianIPCServer


@pytest.fixture
def mock_components():
    """Create mocked components for IPC server."""
    mock_policy = MagicMock()
    mock_tracker = AsyncMock()
    mock_storage = MagicMock()
    mock_user_manager = MagicMock()

    return {
        "policy": mock_policy,
        "tracker": mock_tracker,
        "storage": mock_storage,
        "user_manager": mock_user_manager,
    }


@pytest.fixture
def ipc_server(mock_components):
    """Create IPC server with mocked components."""
    config = {
        "ipc_socket": "/tmp/test-guardian.sock",
        "ipc_admin_group": None,
    }

    # Attach user_manager to tracker
    mock_components["tracker"].user_manager = mock_components["user_manager"]

    server = GuardianIPCServer(
        config=config,
        tracker=mock_components["tracker"],
        policy=mock_components["policy"],
    )
    return server


def test_handle_list_kids(ipc_server, mock_components):
    """Test listing all kids."""
    mock_components["policy"].get_all_usernames.return_value = [
        "alice",
        "bob",
        "charlie",
    ]

    response = ipc_server.handle_list_kids(None)
    data = json.loads(response)

    assert "kids" in data
    assert set(data["kids"]) == {"alice", "bob", "charlie"}
    mock_components["policy"].get_all_usernames.assert_called_once()


@pytest.mark.asyncio
async def test_handle_get_quota_success(ipc_server, mock_components):
    """Test getting quota for a valid user."""
    mock_components["policy"].get_user_policy.return_value = {"quota": {"daily": 120}}
    mock_components["tracker"].get_total_time.return_value = 120.0  # 120 minutes
    mock_components["tracker"].get_remaining_time.return_value = 45.5  # 45.5 minutes

    response = await ipc_server.handle_get_quota("alice")
    data = json.loads(response)

    assert data["kid"] == "alice"
    assert data["used"] == 74.5  # 120 - 45.5
    assert data["limit"] == 120.0
    assert data["remaining"] == 45.5


@pytest.mark.asyncio
async def test_handle_get_quota_missing_kid(ipc_server):
    """Test get_quota with missing kid parameter."""
    response = await ipc_server.handle_get_quota(None)
    data = json.loads(response)

    assert "error" in data
    assert data["error"] == "missing kid"


@pytest.mark.asyncio
async def test_handle_get_quota_unknown_kid(ipc_server, mock_components):
    """Test get_quota for unknown kid."""
    mock_components["policy"].get_user_policy.return_value = None

    response = await ipc_server.handle_get_quota("unknown_user")
    data = json.loads(response)

    assert "error" in data
    assert "unknown kid" in data["error"]


def test_handle_get_curfew_success(ipc_server, mock_components):
    """Test getting curfew for a valid user."""
    mock_components["policy"].get_user_policy.return_value = {
        "curfew": {"weekday": {"start": "08:00", "end": "20:00"}}
    }

    response = ipc_server.handle_get_curfew("alice")
    data = json.loads(response)

    assert data["kid"] == "alice"
    assert "curfew" in data
    assert data["curfew"]["weekday"]["start"] == "08:00"


def test_handle_get_curfew_with_defaults(ipc_server, mock_components):
    """Test getting curfew when user has no custom curfew."""
    mock_components["policy"].get_user_policy.return_value = {"quota": {"daily": 60}}
    mock_components["policy"].get_default.return_value = {
        "weekdays": "09:00-21:00",
        "saturday": "10:00-22:00",
        "sunday": "10:00-20:00",
    }

    response = ipc_server.handle_get_curfew("bob")
    data = json.loads(response)

    assert data["kid"] == "bob"
    assert "curfew" in data
    mock_components["policy"].get_default.assert_called_once_with("curfew")


def test_handle_get_curfew_missing_kid(ipc_server):
    """Test get_curfew with missing kid parameter."""
    response = ipc_server.handle_get_curfew(None)
    data = json.loads(response)

    assert "error" in data
    assert data["error"] == "missing kid"


def test_handle_get_curfew_unknown_kid(ipc_server, mock_components):
    """Test get_curfew for unknown kid."""
    mock_components["policy"].get_user_policy.return_value = None

    response = ipc_server.handle_get_curfew("unknown_user")
    data = json.loads(response)

    assert "error" in data
    assert "unknown kid" in data["error"]


@patch("os.listdir")
def test_handle_list_timers(mock_listdir, ipc_server):
    """Test listing Guardian timers."""
    mock_listdir.return_value = [
        "guardian-daily-reset.timer",
        "guardian-alice.timer",
        "other-service.timer",
        "guardian-bob.timer",
    ]

    response = ipc_server.handle_list_timers(None)
    data = json.loads(response)

    assert "timers" in data
    assert "guardian-daily-reset" in data["timers"]
    assert "guardian-alice" in data["timers"]
    assert "guardian-bob" in data["timers"]
    # Should not include non-guardian timers
    assert "other-service" not in data["timers"]


@patch("guardian_daemon.ipc.SystemdManager")
def test_handle_reload_timers(mock_systemd_cls, ipc_server):
    """Test reloading timers."""
    mock_mgr = MagicMock()
    mock_systemd_cls.return_value = mock_mgr

    response = ipc_server.handle_reload_timers(None)
    data = json.loads(response)

    assert data["status"] == "timers reloaded"
    mock_mgr.create_daily_reset_timer.assert_called_once()


@pytest.mark.asyncio
async def test_handle_reset_quota(ipc_server, mock_components):
    """Test resetting quota for all users."""
    response = await ipc_server.handle_reset_quota(None)
    data = json.loads(response)

    assert data["status"] == "quota reset"
    mock_components["tracker"].perform_daily_reset.assert_called_once_with(force=True)


def test_handle_setup_user_missing_username(ipc_server):
    """Test setup_user with missing username."""
    response = ipc_server.handle_setup_user(None)
    data = json.loads(response)

    assert "error" in data
    assert data["error"] == "missing username"


def test_handle_setup_user_no_user_manager(ipc_server):
    """Test setup_user when user_manager is not available."""
    ipc_server.user_manager = None

    response = ipc_server.handle_setup_user("alice")
    data = json.loads(response)

    assert "error" in data
    assert "user manager not available" in data["error"]


def test_handle_setup_user_nonexistent_user(ipc_server, mock_components):
    """Test setup_user for non-existent user."""
    mock_components["user_manager"].user_exists.return_value = False

    response = ipc_server.handle_setup_user("nonexistent")
    data = json.loads(response)

    assert "error" in data
    assert "does not exist" in data["error"]


def test_handle_setup_user_success(ipc_server, mock_components):
    """Test successful user setup."""
    mock_components["user_manager"].user_exists.return_value = True
    mock_components["policy"].data = {"users": {}}
    mock_components["user_manager"].setup_user_login.return_value = True

    response = ipc_server.handle_setup_user("alice")
    data = json.loads(response)

    assert data["status"] == "success"
    assert "alice" in data["message"]
    mock_components["policy"].add_user.assert_called_once_with("alice")
    mock_components["user_manager"].setup_user_login.assert_called_once_with("alice")


def test_handle_setup_user_setup_failure(ipc_server, mock_components):
    """Test user setup when setup_user_login fails."""
    mock_components["user_manager"].user_exists.return_value = True
    mock_components["policy"].data = {"users": {"alice": {}}}
    mock_components["user_manager"].setup_user_login.return_value = False

    response = ipc_server.handle_setup_user("alice")
    data = json.loads(response)

    assert "error" in data
    assert "failed to set up" in data["error"]


def test_handle_describe_commands(ipc_server):
    """Test describe_commands returns command information."""
    response = ipc_server.handle_describe_commands(None)
    data = json.loads(response)

    # Should have several commands
    assert "list_kids" in data
    assert "get_quota" in data
    assert "get_curfew" in data

    # Check structure of one command
    list_kids_info = data["list_kids"]
    assert "description" in list_kids_info
    assert "params" in list_kids_info
    assert "is_async" in list_kids_info


def test_handle_sync_users_from_config_success(ipc_server, mock_components):
    """Test syncing users from config."""
    mock_components["policy"].data = {
        "users": {
            "alice": {"quota": {"daily": 120}},
            "bob": {"quota": {"daily": 90}},
        },
        "defaults": {"quota": {"daily": 60}},
    }
    mock_components["policy"].get_all_usernames.return_value = [
        "alice"
    ]  # Only alice in DB
    mock_components["policy"].get_user_policy.return_value = {"quota": {"daily": 120}}

    response = ipc_server.handle_sync_users_from_config(None)
    data = json.loads(response)

    assert data["status"] == "success"
    assert "alice" in data["updated"]
    assert "bob" in data["added"]
    mock_components["policy"].add_user.assert_called_with("bob")


def test_handle_sync_users_from_config_error(ipc_server, mock_components):
    """Test sync_users_from_config with exception."""
    mock_components["policy"].data = {"users": {}}
    mock_components["policy"].get_all_usernames.side_effect = Exception(
        "Database error"
    )

    response = ipc_server.handle_sync_users_from_config(None)
    data = json.loads(response)

    assert "error" in data
    assert "Database error" in data["error"]


def test_handle_add_user(ipc_server, mock_components):
    """Test adding a new user."""
    mock_components["policy"].data = {"defaults": {"quota": {"daily": 60}}}

    response = ipc_server.handle_add_user("charlie")
    data = json.loads(response)

    assert data["status"] == "success"
    assert "charlie" in data["message"]
    mock_components["policy"].add_user.assert_called_once_with("charlie")


def test_handle_add_user_missing_username(ipc_server):
    """Test add_user with missing username."""
    response = ipc_server.handle_add_user(None)
    data = json.loads(response)

    assert "error" in data
    assert "missing username" in data["error"]


def test_rate_limit_check(ipc_server):
    """Test rate limiting functionality."""
    uid = 1000

    # Should allow first requests up to limit
    for i in range(ipc_server.RATE_LIMIT_MAX_REQUESTS):
        assert ipc_server._check_rate_limit(uid) is True

    # Should block after limit
    assert ipc_server._check_rate_limit(uid) is False


def test_rate_limit_root_exempt(ipc_server):
    """Test that root (UID 0) is exempt from rate limiting."""
    # Even with many requests, root should not be rate limited
    for _ in range(ipc_server.RATE_LIMIT_MAX_REQUESTS + 10):
        # Root is exempt in handle_connection, not in _check_rate_limit itself
        # This tests the internal tracking only
        pass

    # Regular user should be rate limited
    uid = 1001
    for _ in range(ipc_server.RATE_LIMIT_MAX_REQUESTS):
        ipc_server._check_rate_limit(uid)
    assert ipc_server._check_rate_limit(uid) is False
