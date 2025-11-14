"""
Unit tests for the user_manager module of guardian_daemon.
"""

from unittest.mock import Mock, patch

import pytest

from guardian_daemon.user_manager import UserManager


@pytest.fixture
def user_manager(test_config):
    """Fixture to provide a UserManager instance."""
    config, config_path = test_config
    from guardian_daemon.policy import Policy

    policy = Policy(config_path)
    return UserManager(policy)


def test_validate_username_valid():
    """Test username validation with valid usernames."""
    valid_usernames = [
        "testuser",
        "test_user",
        "test-user",
        "TestUser123",
        "user1",
        "a",
        "abc123_test-user",
    ]

    for username in valid_usernames:
        assert UserManager.validate_username(username), f"'{username}' should be valid"


def test_validate_username_invalid():
    """Test username validation with invalid usernames."""
    invalid_usernames = [
        "",  # Empty
        None,  # None
        "../etc/passwd",  # Path traversal
        "user;rm -rf /",  # Command injection
        "user$(whoami)",  # Command substitution
        "user`whoami`",  # Command substitution
        "user@host",  # Invalid characters
        "user with spaces",  # Spaces
        "user/path",  # Slash
        "user\\path",  # Backslash
        "user*",  # Wildcard
        "user?",  # Question mark
        "user|cmd",  # Pipe
        "user&cmd",  # Ampersand
        "user>file",  # Redirect
        "user<file",  # Redirect
        123,  # Not a string
        [],  # Not a string
    ]

    for username in invalid_usernames:
        assert not UserManager.validate_username(
            username
        ), f"'{username}' should be invalid"


def test_user_exists_with_invalid_username(user_manager):
    """Test that user_exists returns False for invalid usernames."""
    assert not user_manager.user_exists("../etc/passwd")
    assert not user_manager.user_exists("user;rm -rf /")
    assert not user_manager.user_exists("")
    assert not user_manager.user_exists(None)


@patch("guardian_daemon.user_manager.pwd.getpwnam")
def test_user_exists_with_valid_username(mock_getpwnam, user_manager):
    """Test that user_exists works correctly with valid usernames."""
    # Mock user exists
    mock_getpwnam.return_value = Mock(pw_uid=1000, pw_gid=1000, pw_dir="/home/testuser")
    assert user_manager.user_exists("testuser")

    # Mock user doesn't exist
    mock_getpwnam.side_effect = KeyError()
    assert not user_manager.user_exists("nonexistent")


@patch("guardian_daemon.user_manager.pwd.getpwnam")
@patch("guardian_daemon.user_manager.Path")
@patch("guardian_daemon.user_manager.SOURCE_SERVICE_FILE")
def test_setup_user_service_validates_username(
    mock_source, mock_path, mock_getpwnam, user_manager
):
    """Test that setup_user_service rejects invalid usernames."""
    # setup_user_service should reject invalid usernames before calling getpwnam
    user_manager.setup_user_service("../etc/passwd")
    mock_getpwnam.assert_not_called()

    user_manager.setup_user_service("user;rm -rf /")
    mock_getpwnam.assert_not_called()


@patch("guardian_daemon.user_manager.pwd.getpwnam")
@patch("guardian_daemon.user_manager.subprocess.run")
def test_ensure_systemd_user_service_validates_username(
    mock_run, mock_getpwnam, user_manager
):
    """Test that ensure_systemd_user_service rejects invalid usernames."""
    # Should reject invalid usernames before calling getpwnam
    user_manager.ensure_systemd_user_service("../etc/passwd")
    mock_getpwnam.assert_not_called()

    user_manager.ensure_systemd_user_service("user;rm -rf /")
    mock_getpwnam.assert_not_called()


def test_path_traversal_prevention():
    """Test that common path traversal techniques are blocked."""
    path_traversal_attempts = [
        "../../../etc/passwd",
        "..%2F..%2F..%2Fetc%2Fpasswd",  # URL encoded
        "....//....//....//etc/passwd",  # Multiple dots
        "./../..",
        "../../root/.ssh/id_rsa",
    ]

    for attempt in path_traversal_attempts:
        assert not UserManager.validate_username(
            attempt
        ), f"Path traversal blocked: {attempt}"


def test_command_injection_prevention():
    """Test that common command injection techniques are blocked."""
    injection_attempts = [
        "user;cat /etc/passwd",
        "user|whoami",
        "user&whoami",
        "user&&whoami",
        "user||whoami",
        "user`whoami`",
        "user$(whoami)",
        "user${IFS}cat${IFS}/etc/passwd",
    ]

    for attempt in injection_attempts:
        assert not UserManager.validate_username(
            attempt
        ), f"Command injection blocked: {attempt}"
