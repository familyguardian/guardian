"""
Unit tests for enforcer throttling and redundancy prevention.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from guardian_daemon.enforcer import Enforcer


@pytest.fixture
def mock_policy():
    """Create a mock policy object."""
    policy = MagicMock()
    policy.data = {"users": {"testuser": {"quota": {"daily": 120}, "grace_minutes": 5}}}
    return policy


@pytest.fixture
def mock_tracker():
    """Create a mock session tracker."""
    tracker = MagicMock()
    tracker.get_remaining_time = AsyncMock(return_value=30.0)  # 30 minutes
    tracker.get_total_time = AsyncMock(return_value=90.0)
    tracker.session_lock = asyncio.Lock()
    return tracker


@pytest.mark.asyncio
async def test_enforce_user_skips_grace_period_users(mock_policy, mock_tracker):
    """Test that users in grace period are skipped."""
    enforcer = Enforcer(mock_policy, mock_tracker)

    # Add user to grace period
    enforcer._grace_period_users.add("testuser")

    # Enforce should return immediately without calling tracker
    await enforcer.enforce_user("testuser")

    # Verify get_remaining_time was not called
    mock_tracker.get_remaining_time.assert_not_called()


@pytest.mark.asyncio
async def test_enforce_user_runs_on_first_check(mock_policy, mock_tracker):
    """Test that enforcement runs on first check for a user."""
    enforcer = Enforcer(mock_policy, mock_tracker)

    # First enforcement should run
    await enforcer.enforce_user("testuser")

    # Verify tracker methods were called
    mock_tracker.get_remaining_time.assert_called_once()
    mock_tracker.get_total_time.assert_called_once()

    # Verify enforcement check was recorded
    assert "testuser" in enforcer._last_enforcement_check


@pytest.mark.asyncio
async def test_enforce_user_skips_redundant_check(mock_policy, mock_tracker):
    """Test that redundant checks within 30 seconds are skipped."""
    enforcer = Enforcer(mock_policy, mock_tracker)

    # First check
    await enforcer.enforce_user("testuser")
    
    # Second check immediately after (within 30 seconds, same remaining time)
    mock_tracker.get_remaining_time.reset_mock()
    mock_tracker.get_total_time.reset_mock()
    await enforcer.enforce_user("testuser")    # Should be skipped - get_remaining_time called once for check, but not get_total_time
    assert mock_tracker.get_remaining_time.call_count == 1
    mock_tracker.get_total_time.assert_not_called()


@pytest.mark.asyncio
async def test_enforce_user_runs_after_interval(mock_policy, mock_tracker):
    """Test that enforcement runs after the check interval."""
    enforcer = Enforcer(mock_policy, mock_tracker)

    # Set shorter interval for testing
    enforcer._enforcement_check_interval = 0.1

    # First check
    await enforcer.enforce_user("testuser")

    # Wait for interval to pass
    await asyncio.sleep(0.15)

    # Second check should run
    mock_tracker.get_remaining_time.reset_mock()
    mock_tracker.get_total_time.reset_mock()
    await enforcer.enforce_user("testuser")

    # Both methods should be called
    mock_tracker.get_remaining_time.assert_called()
    mock_tracker.get_total_time.assert_called()


@pytest.mark.asyncio
async def test_enforce_user_runs_on_significant_time_change(mock_policy, mock_tracker):
    """Test that enforcement runs when remaining time changes significantly."""
    enforcer = Enforcer(mock_policy, mock_tracker)

    # First check with 30 minutes remaining
    mock_tracker.get_remaining_time.return_value = 30.0
    await enforcer.enforce_user("testuser")

    # Second check immediately but with only 5 minutes remaining (>1 min change)
    mock_tracker.get_remaining_time.return_value = 5.0
    mock_tracker.get_total_time.reset_mock()
    await enforcer.enforce_user("testuser")

    # Should run despite being within interval due to significant time change
    mock_tracker.get_total_time.assert_called()


@pytest.mark.asyncio
async def test_enforce_user_tracks_multiple_users(mock_policy, mock_tracker):
    """Test that enforcement tracking works independently for multiple users."""
    enforcer = Enforcer(mock_policy, mock_tracker)

    # Enforce for user1
    await enforcer.enforce_user("user1")
    assert "user1" in enforcer._last_enforcement_check

    # Enforce for user2
    await enforcer.enforce_user("user2")
    assert "user2" in enforcer._last_enforcement_check

    # Both should be tracked independently
    assert len(enforcer._last_enforcement_check) == 2
    assert (
        enforcer._last_enforcement_check["user1"]
        != enforcer._last_enforcement_check["user2"]
    )


@pytest.mark.asyncio
async def test_grace_period_tracking(mock_policy, mock_tracker):
    """Test that grace period users are properly tracked."""
    enforcer = Enforcer(mock_policy, mock_tracker)

    # Initially no users in grace period
    assert len(enforcer._grace_period_users) == 0

    # Add user to grace period
    enforcer._grace_period_users.add("testuser")

    # Verify user is in set
    assert "testuser" in enforcer._grace_period_users

    # Enforcement should be skipped
    await enforcer.enforce_user("testuser")
    mock_tracker.get_total_time.assert_not_called()


@pytest.mark.asyncio
async def test_enforcement_check_interval_configurable(mock_policy, mock_tracker):
    """Test that enforcement check interval is configurable."""
    enforcer = Enforcer(mock_policy, mock_tracker)

    # Default should be 30 seconds
    assert enforcer._enforcement_check_interval == 30

    # Should be configurable
    enforcer._enforcement_check_interval = 60
    assert enforcer._enforcement_check_interval == 60
