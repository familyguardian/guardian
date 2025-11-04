"""
Unit tests for the sessions module of guardian_daemon.
"""

from datetime import datetime, timedelta

import pytest

from guardian_daemon.policy import Policy
from guardian_daemon.sessions import SessionTracker
from guardian_daemon.storage import Storage


@pytest.fixture
async def user_manager(mocker):
    """Mock UserManager for testing."""
    mock_user_manager = mocker.MagicMock()
    return mock_user_manager


@pytest.fixture
async def session_tracker_instance(test_config, mock_dbus, user_manager):
    """Create a SessionTracker instance for testing."""
    config, config_path = test_config
    mock_bus, mock_logind = mock_dbus

    policy = Policy(config_path)

    return SessionTracker(policy, config, user_manager)


@pytest.mark.asyncio
async def test_session_tracker_init(
    session_tracker_instance, test_config, user_manager
):
    """Test SessionTracker initialization."""
    config, config_path = test_config

    assert isinstance(session_tracker_instance.policy, Policy)
    assert session_tracker_instance.policy.config_path == config_path
    assert isinstance(session_tracker_instance.storage, Storage)
    assert session_tracker_instance.user_manager == user_manager
    assert isinstance(session_tracker_instance.agent_name_map, dict)


@pytest.mark.asyncio
async def test_refresh_agent_name_mapping(
    session_tracker_instance, test_config, mocker
):
    """Test agent name mapping refresh functionality."""
    config, config_path = test_config

    # Mock discover_agent_names_for_user method
    mocker.patch.object(session_tracker_instance, "discover_agent_names_for_user")

    await session_tracker_instance.refresh_agent_name_mapping()

    # Verify discover_agent_names_for_user was called for each user
    assert session_tracker_instance.discover_agent_names_for_user.call_count == len(
        config["users"]
    )


@pytest.mark.asyncio
async def test_check_quota(
    session_tracker_instance, test_config, user_manager, mock_dbus
):
    config, config_path = test_config
    mock_bus, mock_logind = mock_dbus

    policy = Policy(config_path)
    storage = Storage(config["db_path"])
    session_tracker = SessionTracker(policy, storage, user_manager)

    # Test user with quota (60 seconds daily)
    test_user = "test_quota_only"
    now = datetime.now()

    # Add 30 seconds of usage (under quota)
    await storage.add_session_time(test_user, now - timedelta(seconds=30), now)
    quota_ok = await session_tracker.check_quota(test_user)
    assert quota_ok is True, "User should be under quota after 30 seconds"

    # Add another 40 seconds to exceed quota (70 seconds total)
    await storage.add_session_time(test_user, now - timedelta(seconds=40), now)

    quota_ok = await session_tracker.check_quota(test_user)
    assert quota_ok is False


@pytest.mark.asyncio
async def test_check_curfew(test_config, mock_dbus, user_manager):
    """Test curfew checking functionality."""
    config, config_path = test_config
    mock_bus, mock_logind = mock_dbus

    policy = Policy(config_path)
    storage = Storage(config["db_path"])

    session_tracker = SessionTracker(policy, storage, user_manager)

    test_user = "test_full_settings"
    weekday_time = datetime.strptime(
        "15:00", "%H:%M"
    ).time()  # During allowed hours (8:00-20:00)
    weekend_time = datetime.strptime(
        "23:00", "%H:%M"
    ).time()  # After allowed hours (10:00-22:00)

    # Test weekday during allowed hours
    assert (
        await session_tracker.check_curfew(test_user, weekday_time, is_weekend=False)
        is True
    )

    # Test weekend after allowed hours
    assert (
        await session_tracker.check_curfew(test_user, weekend_time, is_weekend=True)
        is False
    )


@pytest.mark.asyncio
async def test_handle_user_new_session(test_config):
    """Test handling user session creation."""
    config, config_path = test_config
