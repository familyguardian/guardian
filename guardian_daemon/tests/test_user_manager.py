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


# Account locking tests


@patch("guardian_daemon.user_manager.subprocess.run")
def test_lock_user_account_success(mock_run, user_manager):
    """Test successful user account locking."""
    # Mock successful usermod -L
    mock_run.return_value = Mock(returncode=0, stderr="")

    result = user_manager.lock_user_account("testuser")

    assert result is True
    mock_run.assert_called_once_with(
        ["usermod", "-L", "testuser"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


@patch("guardian_daemon.user_manager.subprocess.run")
def test_lock_user_account_failure(mock_run, user_manager):
    """Test failed user account locking."""
    # Mock failed usermod -L
    mock_run.return_value = Mock(
        returncode=1, stderr="usermod: user 'testuser' does not exist"
    )

    result = user_manager.lock_user_account("testuser")

    assert result is False


def test_lock_user_account_invalid_username(user_manager):
    """Test that lock_user_account rejects invalid usernames."""
    assert not user_manager.lock_user_account("../etc/passwd")
    assert not user_manager.lock_user_account("user;rm -rf /")
    assert not user_manager.lock_user_account("")
    assert not user_manager.lock_user_account(None)


@patch("guardian_daemon.user_manager.subprocess.run")
def test_lock_user_account_timeout(mock_run, user_manager):
    """Test lock_user_account handles timeouts gracefully."""
    import subprocess

    mock_run.side_effect = subprocess.TimeoutExpired("usermod", 10)

    result = user_manager.lock_user_account("testuser")

    assert result is False


@patch("guardian_daemon.user_manager.subprocess.run")
def test_unlock_user_account_success(mock_run, user_manager):
    """Test successful user account unlocking."""
    # Mock successful usermod -U
    mock_run.return_value = Mock(returncode=0, stderr="")

    result = user_manager.unlock_user_account("testuser")

    assert result is True
    mock_run.assert_called_once_with(
        ["usermod", "-U", "testuser"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


@patch("guardian_daemon.user_manager.subprocess.run")
def test_unlock_user_account_failure(mock_run, user_manager):
    """Test failed user account unlocking."""
    # Mock failed usermod -U
    mock_run.return_value = Mock(
        returncode=1, stderr="usermod: user 'testuser' does not exist"
    )

    result = user_manager.unlock_user_account("testuser")

    assert result is False


def test_unlock_user_account_invalid_username(user_manager):
    """Test that unlock_user_account rejects invalid usernames."""
    assert not user_manager.unlock_user_account("../etc/passwd")
    assert not user_manager.unlock_user_account("user;rm -rf /")
    assert not user_manager.unlock_user_account("")
    assert not user_manager.unlock_user_account(None)


@patch("guardian_daemon.user_manager.subprocess.run")
def test_check_if_locked_user_is_locked(mock_run, user_manager):
    """Test check_if_locked detects locked accounts."""
    # Mock passwd -S output for locked account
    mock_run.return_value = Mock(
        returncode=0, stdout="testuser L 01/01/2024 0 99999 7 -1"
    )

    result = user_manager.check_if_locked("testuser")

    assert result is True
    mock_run.assert_called_once_with(
        ["passwd", "-S", "testuser"],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )


@patch("guardian_daemon.user_manager.subprocess.run")
def test_check_if_locked_user_is_unlocked(mock_run, user_manager):
    """Test check_if_locked detects unlocked accounts."""
    # Mock passwd -S output for unlocked account
    mock_run.return_value = Mock(
        returncode=0, stdout="testuser P 01/01/2024 0 99999 7 -1"
    )

    result = user_manager.check_if_locked("testuser")

    assert result is False


def test_check_if_locked_invalid_username(user_manager):
    """Test that check_if_locked handles invalid usernames."""
    assert not user_manager.check_if_locked("../etc/passwd")
    assert not user_manager.check_if_locked("user;rm -rf /")
    assert not user_manager.check_if_locked("")
    assert not user_manager.check_if_locked(None)


@patch("guardian_daemon.user_manager.subprocess.run")
def test_check_if_locked_command_failure(mock_run, user_manager):
    """Test check_if_locked handles command failures."""
    # Mock failed passwd -S
    mock_run.return_value = Mock(
        returncode=1, stderr="passwd: user 'testuser' does not exist"
    )

    result = user_manager.check_if_locked("testuser")

    assert result is False


@pytest.mark.asyncio
@patch("guardian_daemon.user_manager.UserManager.user_exists")
@patch("guardian_daemon.user_manager.UserManager.check_if_locked")
@patch("guardian_daemon.user_manager.UserManager.lock_user_account")
@patch("guardian_daemon.user_manager.UserManager.unlock_user_account")
async def test_sync_account_locks_locks_user_out_of_time(
    mock_unlock, mock_lock, mock_check_locked, mock_user_exists, user_manager
):
    """Test sync_account_locks locks users who are out of time."""
    # Setup mocks
    mock_user_exists.return_value = True
    mock_check_locked.return_value = False  # Currently unlocked

    # Mock tracker to return no remaining time (async)
    async def mock_get_remaining_time(username):
        return 0

    user_manager.tracker = Mock()
    user_manager.tracker.get_remaining_time = mock_get_remaining_time

    # Mock policy and _is_user_in_curfew to indicate NOT in curfew
    user_manager._is_user_in_curfew = Mock(return_value=False)
    user_manager.policy.data = {"users": {"testuser": {}}}

    await user_manager.sync_account_locks()

    # Should lock the user
    mock_lock.assert_called_once_with("testuser")
    mock_unlock.assert_not_called()


@pytest.mark.asyncio
@patch("guardian_daemon.user_manager.UserManager.user_exists")
@patch("guardian_daemon.user_manager.UserManager.check_if_locked")
@patch("guardian_daemon.user_manager.UserManager.lock_user_account")
@patch("guardian_daemon.user_manager.UserManager.unlock_user_account")
async def test_sync_account_locks_unlocks_user_with_time(
    mock_unlock, mock_lock, mock_check_locked, mock_user_exists, user_manager
):
    """Test sync_account_locks unlocks users who have time remaining."""
    # Setup mocks
    mock_user_exists.return_value = True
    mock_check_locked.return_value = True  # Currently locked

    # Mock tracker to return remaining time (async)
    async def mock_get_remaining_time(username):
        return 30

    user_manager.tracker = Mock()
    user_manager.tracker.get_remaining_time = mock_get_remaining_time

    # Mock _is_user_in_curfew to indicate NOT in curfew
    user_manager._is_user_in_curfew = Mock(return_value=False)
    user_manager.policy.data = {"users": {"testuser": {}}}

    await user_manager.sync_account_locks()

    # Should unlock the user
    mock_unlock.assert_called_once_with("testuser")
    mock_lock.assert_not_called()


@pytest.mark.asyncio
@patch("guardian_daemon.user_manager.UserManager.user_exists")
@patch("guardian_daemon.user_manager.UserManager.check_if_locked")
@patch("guardian_daemon.user_manager.UserManager.lock_user_account")
@patch("guardian_daemon.user_manager.UserManager.unlock_user_account")
async def test_sync_account_locks_locks_even_during_curfew(
    mock_unlock, mock_lock, mock_check_locked, mock_user_exists, user_manager
):
    """Test sync_account_locks locks users even during curfew when quota exhausted."""
    # Setup mocks
    mock_user_exists.return_value = True
    mock_check_locked.return_value = False  # Currently unlocked

    # Mock tracker to return no remaining time (async)
    async def mock_get_remaining_time(username):
        return 0

    user_manager.tracker = Mock()
    user_manager.tracker.get_remaining_time = mock_get_remaining_time

    # Mock _is_user_in_curfew to indicate IN curfew
    user_manager._is_user_in_curfew = Mock(return_value=True)
    user_manager.policy.data = {"users": {"testuser": {}}}

    await user_manager.sync_account_locks()

    # Should lock the user (quota exhausted, even during curfew)
    # This prevents login immediately after curfew ends
    mock_lock.assert_called_once_with("testuser")
    mock_unlock.assert_not_called()


@pytest.mark.asyncio
@patch("guardian_daemon.user_manager.UserManager.user_exists")
@patch("guardian_daemon.user_manager.UserManager.check_if_locked")
@patch("guardian_daemon.user_manager.UserManager.lock_user_account")
@patch("guardian_daemon.user_manager.UserManager.unlock_user_account")
async def test_sync_account_locks_no_change_needed(
    mock_unlock, mock_lock, mock_check_locked, mock_user_exists, user_manager
):
    """Test sync_account_locks skips when lock state is already correct."""
    # Setup mocks
    mock_user_exists.return_value = True
    mock_check_locked.return_value = False  # Currently unlocked

    # Mock tracker to return remaining time (async)
    async def mock_get_remaining_time(username):
        return 30

    user_manager.tracker = Mock()
    user_manager.tracker.get_remaining_time = mock_get_remaining_time

    # Mock _is_user_in_curfew to indicate NOT in curfew
    user_manager._is_user_in_curfew = Mock(return_value=False)
    user_manager.policy.data = {"users": {"testuser": {}}}

    await user_manager.sync_account_locks()

    # Should not lock or unlock (already correct)
    mock_lock.assert_not_called()
    mock_unlock.assert_not_called()


@pytest.mark.asyncio
async def test_sync_account_locks_without_tracker(user_manager):
    """Test sync_account_locks handles missing tracker gracefully."""
    user_manager.tracker = None
    user_manager.policy.data = {"users": {"testuser": {}}}

    # Should not raise an exception
    await user_manager.sync_account_locks()


@patch("guardian_daemon.user_manager.UserManager.user_exists")
@patch("guardian_daemon.user_manager.UserManager.check_if_locked")
@patch("guardian_daemon.user_manager.UserManager.unlock_user_account")
def test_unlock_all_managed_users_success(
    mock_unlock, mock_check_locked, mock_user_exists, user_manager
):
    """Test unlock_all_managed_users unlocks all locked users."""
    # Setup mocks
    mock_user_exists.side_effect = [True, True, True]
    mock_check_locked.side_effect = [True, False, True]  # 2 locked, 1 unlocked
    mock_unlock.return_value = True

    user_manager.policy.data = {"users": {"user1": {}, "user2": {}, "user3": {}}}

    result = user_manager.unlock_all_managed_users()

    # Should unlock only the locked users (user1 and user3)
    assert result == 2
    assert mock_unlock.call_count == 2


@patch("guardian_daemon.user_manager.UserManager.user_exists")
def test_unlock_all_managed_users_no_users(mock_user_exists, user_manager):
    """Test unlock_all_managed_users with no managed users."""
    user_manager.policy.data = {"users": {}}

    result = user_manager.unlock_all_managed_users()

    assert result == 0


@patch("guardian_daemon.user_manager.UserManager.user_exists")
@patch("guardian_daemon.user_manager.UserManager.check_if_locked")
def test_unlock_all_managed_users_skips_nonexistent(
    mock_check_locked, mock_user_exists, user_manager
):
    """Test unlock_all_managed_users skips non-existent users."""
    # Setup mocks
    mock_user_exists.side_effect = [False, True]
    mock_check_locked.return_value = False

    user_manager.policy.data = {"users": {"nonexistent": {}, "testuser": {}}}

    result = user_manager.unlock_all_managed_users()

    # Should check only the existing user
    assert mock_check_locked.call_count == 1
    assert result == 0
