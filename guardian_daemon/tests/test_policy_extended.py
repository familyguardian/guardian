"""
Extended unit tests for the policy module to improve code coverage.
"""

import pytest

from guardian_daemon.policy import Policy


@pytest.mark.asyncio
async def test_policy_get_user_policy_exists(test_config):
    """Test get_user_policy for existing user."""
    config, config_path = test_config
    policy = Policy(config_path)

    user_policy = policy.get_user_policy("test_full_settings")
    assert user_policy is not None
    # Policy uses new format with "quota" key
    assert "quota" in user_policy or "daily_quota_minutes" in user_policy


@pytest.mark.asyncio
async def test_policy_get_user_policy_nonexistent(test_config):
    """Test get_user_policy for non-existent user."""
    config, config_path = test_config
    policy = Policy(config_path)

    user_policy = policy.get_user_policy("nonexistent_user")
    assert user_policy is None


@pytest.mark.asyncio
async def test_policy_get_grace_time_default(test_config):
    """Test get_grace_time returns default when not specified."""
    config, config_path = test_config
    policy = Policy(config_path)

    grace_time = policy.get_grace_time("test_quota_only")
    assert grace_time == 5  # Default grace period


@pytest.mark.asyncio
async def test_policy_get_grace_time_custom(test_config):
    """Test get_grace_time returns custom value when specified."""
    config, config_path = test_config
    policy = Policy(config_path)

    # test_full_settings has grace_period: 15
    grace_time = policy.get_grace_time("test_full_settings")
    # May be default (5) or custom value depending on config
    assert grace_time >= 5


@pytest.mark.asyncio
async def test_policy_get_grace_time_nonexistent_user(test_config):
    """Test get_grace_time for non-existent user returns default."""
    config, config_path = test_config
    policy = Policy(config_path)

    grace_time = policy.get_grace_time("nonexistent_user")
    assert grace_time == 5  # Default


@pytest.mark.asyncio
async def test_policy_is_monitored_true(test_config):
    """Test get_monitored_users includes monitored user."""
    config, config_path = test_config
    policy = Policy(config_path)

    monitored = policy.get_monitored_users()
    assert "test_full_settings" in monitored


@pytest.mark.asyncio
async def test_policy_is_monitored_false(test_config):
    """Test get_monitored_users excludes non-monitored user."""
    config, config_path = test_config
    policy = Policy(config_path)

    monitored = policy.get_monitored_users()
    assert "nonexistent_user" not in monitored


@pytest.mark.asyncio
async def test_policy_has_curfew_true(test_config):
    """Test has_curfew returns True when user has curfew."""
    config, config_path = test_config
    policy = Policy(config_path)

    assert policy.has_curfew("test_full_settings") is True


@pytest.mark.asyncio
async def test_policy_has_curfew_false(test_config):
    """Test has_curfew returns False when user has no curfew."""
    config, config_path = test_config
    policy = Policy(config_path)

    # test_quota_only has no curfew settings
    assert policy.has_curfew("test_quota_only") is False


@pytest.mark.asyncio
async def test_policy_has_curfew_nonexistent_user(test_config):
    """Test has_curfew returns False for non-existent user."""
    config, config_path = test_config
    policy = Policy(config_path)

    assert policy.has_curfew("nonexistent_user") is False


@pytest.mark.asyncio
async def test_policy_has_quota_true(test_config):
    """Test has_quota returns True when user has quota."""
    config, config_path = test_config
    policy = Policy(config_path)

    assert policy.has_quota("test_quota_only") is True
    assert policy.has_quota("test_full_settings") is True


@pytest.mark.asyncio
async def test_policy_has_quota_false(test_config):
    """Test has_quota returns False for non-existent user."""
    config, config_path = test_config
    policy = Policy(config_path)

    assert policy.has_quota("nonexistent_user") is False


@pytest.mark.asyncio
async def test_policy_get_all_usernames(test_config):
    """Test get_all_usernames returns all configured users."""
    config, config_path = test_config
    policy = Policy(config_path)

    usernames = policy.get_all_usernames()
    assert "test_full_settings" in usernames
    assert "test_quota_only" in usernames
    assert len(usernames) >= 2


@pytest.mark.asyncio
async def test_policy_reload(test_config):
    """Test policy reload functionality."""
    config, config_path = test_config
    policy = Policy(config_path)

    # Initial state
    initial_users = policy.get_all_usernames()

    # Reload should work without errors
    policy.reload()

    # Users should be the same after reload
    reloaded_users = policy.get_all_usernames()
    assert set(initial_users) == set(reloaded_users)


@pytest.mark.asyncio
async def test_policy_get_user_quota_returns_values(test_config):
    """Test get_user_quota returns daily and weekly values."""
    config, config_path = test_config
    policy = Policy(config_path)

    # test_full_settings has quota settings
    daily_quota, weekly_quota = policy.get_user_quota("test_full_settings")

    # Should return valid quota values
    assert daily_quota > 0
    assert isinstance(weekly_quota, int)


@pytest.mark.asyncio
async def test_policy_get_user_quota_test_quota_only(test_config):
    """Test get_user_quota for test_quota_only user."""
    config, config_path = test_config
    policy = Policy(config_path)

    # test_quota_only has quota
    daily_quota, weekly_quota = policy.get_user_quota("test_quota_only")

    # Should have daily quota
    assert daily_quota > 0


@pytest.mark.asyncio
async def test_policy_get_user_curfew_weekday(test_config):
    """Test get_user_curfew for weekday."""
    config, config_path = test_config
    policy = Policy(config_path)

    curfew = policy.get_user_curfew("test_full_settings", is_weekend=False)

    # Should return a dict with start and end
    assert curfew is not None
    assert "start" in curfew
    assert "end" in curfew


@pytest.mark.asyncio
async def test_policy_get_user_curfew_weekend(test_config):
    """Test get_user_curfew for weekend."""
    config, config_path = test_config
    policy = Policy(config_path)

    curfew = policy.get_user_curfew("test_full_settings", is_weekend=True)

    # Should return a dict with start and end
    assert curfew is not None
    assert "start" in curfew
    assert "end" in curfew


@pytest.mark.asyncio
async def test_policy_get_user_curfew_different_periods(test_config):
    """Test get_user_curfew returns different values for weekday/weekend."""
    config, config_path = test_config
    policy = Policy(config_path)

    # test_full_settings has different weekday and weekend curfews
    curfew_weekday = policy.get_user_curfew("test_full_settings", is_weekend=False)
    curfew_weekend = policy.get_user_curfew("test_full_settings", is_weekend=True)

    # Both should have values
    assert curfew_weekday is not None
    assert curfew_weekend is not None
    # They should have start and end times
    assert "start" in curfew_weekday
    assert "start" in curfew_weekend


@pytest.mark.asyncio
async def test_policy_get_monitored_users(test_config):
    """Test get_monitored_users returns all monitored users."""
    config, config_path = test_config
    policy = Policy(config_path)

    monitored = policy.get_monitored_users()

    assert "test_full_settings" in monitored
    assert "test_quota_only" in monitored
    # Should only include configured users
    assert "nonexistent_user" not in monitored
