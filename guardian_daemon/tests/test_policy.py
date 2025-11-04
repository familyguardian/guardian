"""
Unit tests for the policy module of guardian_daemon.
"""

import pytest

from guardian_daemon.policy import Policy


def test_policy_load(test_config):
    """Test policy loading from configuration file."""
    config, config_path = test_config

    policy = Policy(config_path)
    assert policy.data == config
    assert "users" in policy.data

    # Check for existence of test users with different configurations
    assert "test_full_settings" in policy.data["users"]
    assert "test_quota_only" in policy.data["users"]
    assert "test_minimal" in policy.data["users"]
    assert "test_quota_exempt" in policy.data["users"]


def test_policy_get_user_quota(test_config):
    """Test retrieving user quota settings."""
    config, config_path = test_config
    policy = Policy(config_path)

    # Test user with full quota settings
    daily, weekly = policy.get_user_quota("test_full_settings")
    assert daily == 120
    assert weekly == 600

    # Test user with basic quota
    daily, weekly = policy.get_user_quota("test_quota_only")
    assert daily == 60
    assert weekly == 300

    # Test user with minimal settings
    daily, weekly = policy.get_user_quota("test_minimal")
    assert daily == 30
    assert weekly == 0  # No weekly quota defined

    # Test non-existent user
    with pytest.raises(KeyError):
        policy.get_user_quota("nonexistent")


def test_policy_get_user_curfew(test_config):
    """Test retrieving user curfew settings."""
    config, config_path = test_config
    policy = Policy(config_path)

    # Test user with full settings
    weekday = policy.get_user_curfew("test_full_settings", is_weekend=False)
    assert weekday["start"] == "08:00"
    assert weekday["end"] == "20:00"

    weekend = policy.get_user_curfew("test_full_settings", is_weekend=True)
    assert weekend["start"] == "10:00"
    assert weekend["end"] == "22:00"

    # Test user with only weekday curfew
    weekday = policy.get_user_curfew("test_weekday_curfew", is_weekend=False)
    assert weekday["start"] == "09:00"
    assert weekday["end"] == "21:00"
    assert policy.get_user_curfew("test_weekday_curfew", is_weekend=True) is None

    # Test user without curfew settings
    assert policy.get_user_curfew("test_minimal", is_weekend=False) is None
    assert policy.get_user_curfew("test_minimal", is_weekend=True) is None


def test_policy_has_quota(test_config):
    """Test checking if a user has a quota."""
    config, config_path = test_config
    policy = Policy(config_path)

    # Test users with different quota configurations
    assert policy.has_quota("test_full_settings") is True
    assert policy.has_quota("test_quota_only") is True
    assert policy.has_quota("test_minimal") is True
    assert policy.has_quota("test_quota_exempt") is False
    assert policy.has_quota("nonexistent") is False


def test_policy_has_curfew(test_config):
    """Test checking if user has curfew settings."""
    config, config_path = test_config
    policy = Policy(config_path)

    assert policy.has_curfew("test_full_settings") is True
    assert policy.has_curfew("test_weekday_curfew") is True
    assert policy.has_curfew("test_minimal") is False
    assert policy.has_curfew("test_quota_only") is False
    assert policy.has_curfew("nonexistent") is False


def test_policy_get_monitored_users(test_config):
    """Test retrieving list of monitored users."""
    config, config_path = test_config
    policy = Policy(config_path)

    users = policy.get_monitored_users()
    assert isinstance(users, list)

    # Check for users that should be monitored
    assert "test_full_settings" in users
    assert "test_quota_only" in users
    assert "test_minimal" in users
    assert "test_weekday_curfew" in users

    # Check for users that should not be monitored
    assert "test_quota_exempt" not in users
    assert "test_unmonitored" not in users

    # Verify total number of monitored users
    assert len(users) == 4  # Number of test users that should be monitored
