"""
Test notification timing for enforcer.

This test verifies that users receive notifications at:
- 15 minutes before logout
- 10 minutes before logout
- 5 minutes before logout
- Then minutely from 4 minutes down to 1 minute
- Then grace period with minutely notifications
"""

from unittest.mock import AsyncMock

import pytest

from guardian_daemon.enforcer import Enforcer
from guardian_daemon.policy import Policy
from guardian_daemon.sessions import SessionTracker


@pytest.fixture
def enforcer_for_notifications(test_config, mock_dbus, mocker):
    """Fixture to provide an enforcer instance for notification testing."""
    config, config_path = test_config
    mock_bus, mock_logind = mock_dbus

    mock_policy = mocker.MagicMock(spec=Policy)
    mock_session_tracker = mocker.MagicMock(spec=SessionTracker)

    # Mock policy methods
    mock_policy.get_grace_time = mocker.MagicMock(return_value=5)

    enforcer = Enforcer(mock_policy, mock_session_tracker)

    # Mock notify_user to track notifications
    enforcer.notify_user = AsyncMock()

    return enforcer, mock_session_tracker, mock_policy


@pytest.mark.asyncio
async def test_notification_at_15_minutes(enforcer_for_notifications):
    """Test that notification is sent at 15 minutes remaining."""
    enforcer_instance, mock_tracker, _ = enforcer_for_notifications

    username = "testuser"
    mock_tracker.get_remaining_time.return_value = 15.0
    mock_tracker.get_total_time.return_value = 60.0

    await enforcer_instance.enforce_user(username)

    # Should have sent 15 minute notification
    enforcer_instance.notify_user.assert_awaited_once()
    call_args = enforcer_instance.notify_user.await_args
    assert "15 minutes left!" in call_args[0][1]
    assert call_args[1]["category"] == "info"


@pytest.mark.asyncio
async def test_notification_at_10_minutes(enforcer_for_notifications):
    """Test that notification is sent at 10 minutes remaining."""
    enforcer_instance, mock_tracker, _ = enforcer_for_notifications

    username = "testuser"
    mock_tracker.get_remaining_time.return_value = 10.0
    mock_tracker.get_total_time.return_value = 60.0

    await enforcer_instance.enforce_user(username)

    # Should have sent 10 minute notification
    enforcer_instance.notify_user.assert_awaited_once()
    call_args = enforcer_instance.notify_user.await_args
    assert "10 minutes left!" in call_args[0][1]
    assert call_args[1]["category"] == "warning"


@pytest.mark.asyncio
async def test_notification_at_5_minutes(enforcer_for_notifications):
    """Test that notification is sent at 5 minutes remaining."""
    enforcer_instance, mock_tracker, _ = enforcer_for_notifications

    username = "testuser"
    mock_tracker.get_remaining_time.return_value = 5.0
    mock_tracker.get_total_time.return_value = 60.0

    await enforcer_instance.enforce_user(username)

    # Should have sent 5 minute notification
    enforcer_instance.notify_user.assert_awaited_once()
    call_args = enforcer_instance.notify_user.await_args
    assert "5 minutes left!" in call_args[0][1]
    assert call_args[1]["category"] == "warning"


@pytest.mark.asyncio
async def test_minutely_notifications_4_to_1(enforcer_for_notifications):
    """Test that minutely notifications are sent from 4 to 1 minutes."""
    enforcer_instance, mock_tracker, _ = enforcer_for_notifications

    username = "testuser"
    mock_tracker.get_total_time.return_value = 60.0

    # Test each minute from 4 down to 1
    for minutes in [4, 3, 2, 1]:
        enforcer_instance.notify_user.reset_mock()
        mock_tracker.get_remaining_time.return_value = float(minutes)

        await enforcer_instance.enforce_user(username)

        # Should have sent notification for this minute
        enforcer_instance.notify_user.assert_awaited_once()
        call_args = enforcer_instance.notify_user.await_args
        assert f"{minutes} minute" in call_args[0][1]
        assert call_args[1]["category"] == "critical"


@pytest.mark.asyncio
async def test_notification_cooldowns(enforcer_for_notifications):
    """Test that notifications have appropriate cooldowns.

    - Notifications at 15, 10, 5 minutes have 5-minute cooldown
    - Minutely notifications (4-1) have 1-minute cooldown
    """
    enforcer_instance, mock_tracker, _ = enforcer_for_notifications

    username = "testuser"
    mock_tracker.get_total_time.return_value = 60.0

    # Test 15 minute notification cooldown (5 minutes)
    mock_tracker.get_remaining_time.return_value = 15.0
    await enforcer_instance.enforce_user(username)
    assert enforcer_instance.notify_user.await_count == 1

    # Immediate re-check should not send another notification
    await enforcer_instance.enforce_user(username)
    assert enforcer_instance.notify_user.await_count == 1  # Still 1, not 2

    # Test minutely notification cooldown (1 minute = 60 seconds)
    enforcer_instance.notify_user.reset_mock()
    enforcer_instance._last_notifications.clear()

    mock_tracker.get_remaining_time.return_value = 3.0
    await enforcer_instance.enforce_user(username)
    assert enforcer_instance.notify_user.await_count == 1

    # Immediate re-check should not send another notification
    await enforcer_instance.enforce_user(username)
    assert enforcer_instance.notify_user.await_count == 1  # Still 1


@pytest.mark.asyncio
async def test_full_notification_sequence(enforcer_for_notifications, mocker):
    """Test the complete notification sequence from 15 minutes to grace period."""
    enforcer_instance, mock_tracker, mock_policy = enforcer_for_notifications

    # Mock asyncio.sleep to prevent hanging
    mocker.patch("asyncio.sleep", new_callable=AsyncMock)

    username = "testuser"
    mock_tracker.get_total_time.return_value = 60.0
    mock_policy.get_grace_time.return_value = 2  # Short grace for testing

    notification_sequence = []

    # Track notifications
    async def track_notification(user, message, category=None):
        notification_sequence.append((user, message, category))

    enforcer_instance.notify_user = AsyncMock(side_effect=track_notification)
    enforcer_instance.terminate_session = AsyncMock()

    # Simulate time progression
    time_points = [15.0, 10.0, 5.0, 4.0, 3.0, 2.0, 1.0, 0.0]

    for remaining_time in time_points:
        mock_tracker.get_remaining_time.return_value = remaining_time
        await enforcer_instance.enforce_user(username)

        # Clear notification tracking between checks
        enforcer_instance._last_enforcement_check.clear()

    # Verify we got notifications at expected times
    messages = [msg for _, msg, _ in notification_sequence]

    # Should have notifications for 15, 10, 5, 4, 3, 2, 1 minutes
    assert any("15 minutes left!" in msg for msg in messages)
    assert any("10 minutes left!" in msg for msg in messages)
    assert any("5 minutes left!" in msg for msg in messages)
    assert any("4 minutes left!" in msg for msg in messages)
    assert any("3 minutes left!" in msg for msg in messages)
    assert any("2 minutes left!" in msg for msg in messages)
    assert any("1 minute left!" in msg for msg in messages)

    # Grace period should have started at 0 minutes
    assert any("Time over!" in msg for msg in messages)
    assert any("grace time left!" in msg for msg in messages)
