"""
Unit tests for the enforcer module of guardian_daemon.
"""

from unittest.mock import AsyncMock

import pytest

from guardian_daemon.enforcer import Enforcer
from guardian_daemon.policy import Policy
from guardian_daemon.sessions import SessionTracker


@pytest.fixture
def enforcer(test_config, mock_dbus, mocker):
    """Fixture to provide an enforcer instance with mocked dependencies."""
    config, config_path = test_config
    mock_bus, mock_logind = mock_dbus

    # Mock components
    mock_policy = mocker.MagicMock(spec=Policy)
    mock_session_tracker = mocker.MagicMock(spec=SessionTracker)
    mock_session_tracker.check_quota = AsyncMock(return_value=True)
    mock_session_tracker.check_curfew = AsyncMock(return_value=True)
    mock_user_manager = mocker.MagicMock()

    # Mock policy methods
    mock_policy.get_grace_time = mocker.MagicMock(
        return_value=5
    )  # 5 minutes grace period

    # Patch UserManager constructor
    mocker.patch("guardian_daemon.enforcer.UserManager", return_value=mock_user_manager)

    enforcer = Enforcer(mock_policy, mock_session_tracker)
    return enforcer, mock_session_tracker, mock_policy


@pytest.mark.asyncio
async def test_enforcer_init(enforcer):
    """Test enforcer initialization."""
    enforcer_instance, mock_tracker, mock_policy = enforcer
    assert enforcer_instance.tracker == mock_tracker
    assert enforcer_instance.policy == mock_policy


@pytest.mark.asyncio
async def test_enforce_user_grace_period(enforcer, mocker):
    """Test enforcement when a user enters grace period."""
    enforcer_instance, mock_tracker, mock_policy = enforcer

    # Mock asyncio.sleep to prevent hanging
    mock_sleep = mocker.patch("asyncio.sleep", new_callable=AsyncMock)
    # Mock handle_grace_period to capture grace period activation
    handle_grace_spy = mocker.spy(enforcer_instance, "handle_grace_period")

    username = "testuser"
    # Mock getting remaining time
    mock_tracker.get_remaining_time.return_value = 0
    mock_tracker.get_total_time.return_value = 3600  # 1 hour total
    mock_policy.get_grace_time.return_value = 5

    # Start enforcing
    await enforcer_instance.enforce_user(username)

    # Verify method calls
    mock_tracker.get_remaining_time.assert_called_with(username)
    mock_policy.get_grace_time.assert_called_with(username)

    # Verify grace period was handled
    handle_grace_spy.assert_called_once_with(username)
    mock_sleep.assert_awaited_with(60)  # Should be called for each minute of grace time
    assert (
        mock_sleep.await_count == 5
    )  # Should be called 5 times for 5 minutes grace period


@pytest.mark.asyncio
async def test_enforce_user_with_notifications(enforcer):
    """Test enforcement when user has time remaining."""
    enforcer_instance, mock_tracker, _ = enforcer

    username = "testuser"
    # Mock getting remaining time
    mock_tracker.get_remaining_time.return_value = 300  # 5 minutes remaining
    mock_tracker.get_total_time.return_value = 3600  # 1 hour total

    # Test notification flow
    await enforcer_instance.enforce_user(username)
    mock_tracker.get_remaining_time.assert_called_with(username)
    assert username not in enforcer_instance._grace_period_users


@pytest.mark.asyncio
async def test_notification_cooldown(enforcer):
    """Test that notifications respect cooldown periods."""
    enforcer_instance, mock_tracker, _ = enforcer

    username = "testuser"
    mock_tracker.get_remaining_time.return_value = 300  # 5 minutes remaining
    mock_tracker.get_total_time.return_value = 3600  # 1 hour total

    # First notification
    await enforcer_instance.enforce_user(username)

    # Second immediate notification should be blocked by cooldown
    await enforcer_instance.enforce_user(username)

    # Check that notification state is tracked
    assert "5min" in enforcer_instance._last_notifications[username]


@pytest.mark.asyncio
async def test_handle_grace_period(enforcer, mocker):
    """Test grace period handling."""
    enforcer_instance, mock_tracker, mock_policy = enforcer

    # Mock asyncio.sleep to prevent hanging
    mock_sleep = mocker.patch("asyncio.sleep", new_callable=AsyncMock)
    mock_notify = mocker.patch.object(
        enforcer_instance, "notify_user", new_callable=AsyncMock
    )
    mock_terminate = mocker.patch.object(
        enforcer_instance, "terminate_session", new_callable=AsyncMock
    )

    username = "testuser"
    grace_time = 5
    mock_policy.get_grace_time.return_value = grace_time

    # Add user to grace period set before testing handle_grace_period
    enforcer_instance._grace_period_users.add(username)

    # Test handle_grace_period directly
    await enforcer_instance.handle_grace_period(username)

    # Verify sleep was called for each minute
    assert mock_sleep.await_count == grace_time
    mock_sleep.assert_awaited_with(60)

    # Verify notifications were sent
    # There should be grace_time notifications for countdown
    # plus 1 initial "Time over!" notification
    expected_notify_count = grace_time + 1
    assert mock_notify.await_count == expected_notify_count

    # Verify countdown notifications
    for i in range(grace_time):
        minutes_left = grace_time - i
        mock_notify.assert_any_await(
            username,
            f"{minutes_left} minutes of grace time left! Save your work.",
            category="critical",
        )

    # Verify session was terminated at the end
    mock_terminate.assert_awaited_once_with(username)

    # Grace period should be active
    assert username in enforcer_instance._grace_period_users
