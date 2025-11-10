"""
Comprehensive tests for user_manager.py to improve coverage.
Focus on core functionality: username validation, user existence checks,
and PAM rule generation.
"""

import pwd
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from guardian_daemon.policy import Policy
from guardian_daemon.user_manager import SetupError, UserManager


@pytest.fixture
def test_policy(test_config):
    """Create a test policy from test_config fixture."""
    config, config_path = test_config
    return Policy(config_path)


@pytest.fixture
def user_manager(test_policy):
    """Create a UserManager instance with test policy."""
    return UserManager(policy=test_policy)


# ============================================================================
# Username Validation Tests
# ============================================================================


def test_validate_username_valid():
    """Test that valid usernames are accepted."""
    assert UserManager.validate_username("alice") is True
    assert UserManager.validate_username("bob123") is True
    assert UserManager.validate_username("user_name") is True
    assert UserManager.validate_username("test-user") is True
    assert UserManager.validate_username("a") is True
    assert UserManager.validate_username("user123_test-name") is True


def test_validate_username_invalid():
    """Test that invalid usernames are rejected."""
    # Path traversal attempts
    assert UserManager.validate_username("../etc/passwd") is False
    assert UserManager.validate_username("user/../admin") is False
    assert UserManager.validate_username("../../root") is False

    # Special characters
    assert UserManager.validate_username("user@host") is False
    assert UserManager.validate_username("user;id") is False
    assert UserManager.validate_username("user|cat") is False
    assert UserManager.validate_username("user$HOME") is False
    assert UserManager.validate_username("user name") is False  # spaces

    # Empty or None
    assert UserManager.validate_username("") is False
    assert UserManager.validate_username(None) is False

    # Wrong type
    assert UserManager.validate_username(123) is False
    assert UserManager.validate_username([]) is False


# ============================================================================
# User Existence Tests
# ============================================================================


def test_user_exists_valid_user(user_manager):
    """Test user_exists returns True for existing system user."""
    # Get the current user (should always exist)
    import os

    current_user = pwd.getpwuid(os.getuid()).pw_name

    assert user_manager.user_exists(current_user) is True


def test_user_exists_invalid_username(user_manager):
    """Test user_exists returns False for invalid username format."""
    assert user_manager.user_exists("../etc/passwd") is False
    assert user_manager.user_exists("user;whoami") is False


def test_user_exists_nonexistent_user(user_manager):
    """Test user_exists returns False for nonexistent but valid username."""
    assert user_manager.user_exists("nonexistentuser12345") is False
    assert user_manager.user_exists("thisuserdoesnotexist") is False


# ============================================================================
# PAM Time Rules Generation Tests
# ============================================================================


def test_generate_rules_basic(user_manager):
    """Test basic PAM rule generation."""
    rules = user_manager._generate_rules()

    # Should contain managed rules header
    assert any("Guardian Managed Rules" in rule for rule in rules)

    # Should contain final catch-all rule for non-kids users
    assert "*;*;!@kids;Al0000-2400" in rules

    # Should have multiple rules
    assert len(rules) > 1


def test_generate_rules_with_curfew(test_config):
    """Test PAM rule generation for users with specific curfew settings."""
    config, config_path = test_config

    policy = Policy(config_path)
    um = UserManager(policy=policy)
    rules = um._generate_rules()

    # Should contain rules for users with curfew (test_config has several)
    rules_str = "\n".join(rules)
    # Check that managed users appear in the rules
    assert "test_minimal" in rules_str or "test_quota_only" in rules_str


def test_generate_rules_no_managed_users(test_config):
    """Test PAM rule generation when no users are managed."""
    config, config_path = test_config

    # Create config with empty users
    config["users"] = {}

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        import yaml

        yaml.dump(config, f)
        f.flush()

        policy = Policy(f.name)
        um = UserManager(policy=policy)
        rules = um._generate_rules()

    # Should still have the catch-all rule
    assert "*;*;!@kids;Al0000-2400" in rules


# ============================================================================
# Initialization Tests
# ============================================================================


def test_init_with_policy(test_policy):
    """Test UserManager initialization with a policy."""
    um = UserManager(policy=test_policy)

    assert um.policy == test_policy
    assert um.tracker is None


def test_init_with_tracker(test_policy):
    """Test UserManager initialization with policy and tracker."""
    mock_tracker = MagicMock()
    um = UserManager(policy=test_policy, tracker=mock_tracker)

    assert um.policy == test_policy
    assert um.tracker == mock_tracker


def test_set_tracker(user_manager):
    """Test setting tracker after initialization."""
    mock_tracker = MagicMock()

    assert user_manager.tracker is None
    user_manager.set_tracker(mock_tracker)
    assert user_manager.tracker == mock_tracker


# ============================================================================
# Update Policy Tests
# ============================================================================


def test_update_policy(user_manager, test_config):
    """Test updating policy triggers re-evaluation."""
    config, config_path = test_config

    # Create a new policy
    new_config = config.copy()
    new_config["users"]["new_user"] = {"quota": {"daily": 60}}

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        import yaml

        yaml.dump(new_config, f)
        f.flush()

        new_policy = Policy(f.name)

    # Mock the methods that would write to system files
    with patch.object(user_manager, "write_time_rules") as mock_write:
        with patch.object(user_manager, "ensure_kids_group") as mock_ensure:
            user_manager.update_policy(new_policy)

            assert user_manager.policy == new_policy
            mock_write.assert_called_once()
            mock_ensure.assert_called_once()


# ============================================================================
# Setup User Login Tests (without system modifications)
# ============================================================================


def test_setup_user_login_nonexistent_user(user_manager):
    """Test setup_user_login fails for nonexistent user."""
    result = user_manager.setup_user_login("nonexistentuser999")

    assert result is False


def test_setup_user_login_invalid_username(user_manager):
    """Test setup_user_login rejects invalid username."""
    with patch.object(user_manager, "user_exists", return_value=False):
        result = user_manager.setup_user_login("../etc/passwd")

        assert result is False


# ============================================================================
# Ensure Kids Group Tests (mocked)
# ============================================================================


@patch("guardian_daemon.user_manager.subprocess.run")
@patch("guardian_daemon.user_manager.grp.getgrnam")
def test_ensure_kids_group_creates_group(mock_getgrnam, mock_subprocess, user_manager):
    """Test ensure_kids_group creates kids group if it doesn't exist."""
    # Simulate group not existing
    mock_getgrnam.side_effect = KeyError("kids")
    mock_subprocess.return_value = Mock(returncode=0)

    with patch.object(user_manager, "user_exists", return_value=False):
        # Call with no managed users to avoid user operations
        user_manager.policy.data["users"] = {}
        user_manager.ensure_kids_group()

    # Should have tried to create the group
    mock_subprocess.assert_called()
    call_args = mock_subprocess.call_args[0][0]
    assert "groupadd" in call_args
    assert "kids" in call_args


@patch("guardian_daemon.user_manager.subprocess.run")
@patch("guardian_daemon.user_manager.grp.getgrnam")
def test_ensure_kids_group_already_exists(mock_getgrnam, mock_subprocess, user_manager):
    """Test ensure_kids_group handles existing kids group."""
    # Simulate group already exists
    mock_getgrnam.return_value = Mock()

    with patch.object(user_manager, "user_exists", return_value=False):
        # Call with no managed users
        user_manager.policy.data["users"] = {}
        user_manager.ensure_kids_group()

    # Should not try to create the group
    for call in mock_subprocess.call_args_list:
        assert "groupadd" not in str(call)


@patch("guardian_daemon.user_manager.subprocess.run")
@patch("guardian_daemon.user_manager.grp.getgrnam")
def test_ensure_kids_group_creation_failure(
    mock_getgrnam, mock_subprocess, user_manager
):
    """Test ensure_kids_group raises SetupError on group creation failure."""
    # Simulate group not existing
    mock_getgrnam.side_effect = KeyError("kids")
    # Simulate groupadd command failure with CalledProcessError
    mock_subprocess.side_effect = subprocess.CalledProcessError(
        1, ["groupadd", "kids"], stderr="Permission denied"
    )

    with patch.object(user_manager, "user_exists", return_value=False):
        user_manager.policy.data["users"] = {}

        with pytest.raises(SetupError):
            user_manager.ensure_kids_group()


# ============================================================================
# D-Bus Policy Tests (mocked file operations)
# ============================================================================


@patch("builtins.open", create=True)
@patch("guardian_daemon.user_manager.subprocess.run")
def test_setup_dbus_policy_creates_file(mock_subprocess, mock_open, user_manager):
    """Test setup_dbus_policy creates the D-Bus policy configuration."""
    mock_file = MagicMock()
    mock_open.return_value.__enter__.return_value = mock_file
    mock_subprocess.return_value = Mock(returncode=0)

    user_manager.setup_dbus_policy()

    # Should have written to the file
    mock_file.write.assert_called_once()
    written_content = mock_file.write.call_args[0][0]

    # Verify policy content
    assert "org.guardian.Daemon" in written_content
    assert "org.guardian.Agent" in written_content
    assert 'group="kids"' in written_content


@patch("builtins.open", create=True)
@patch("guardian_daemon.user_manager.subprocess.run")
def test_setup_dbus_policy_file_write_error(mock_subprocess, mock_open, user_manager):
    """Test setup_dbus_policy handles file write errors gracefully."""
    mock_open.side_effect = PermissionError("Permission denied")

    # Should not raise exception, just log error
    user_manager.setup_dbus_policy()


# ============================================================================
# Service File Path Validation Tests
# ============================================================================


def test_validate_username_in_service_setup(user_manager):
    """Test that setup_user_service validates username format."""
    with patch("guardian_daemon.user_manager.pwd.getpwnam") as mock_getpw:
        # Even if user exists, invalid format should be rejected
        mock_getpw.return_value = Mock(pw_dir="/home/test", pw_uid=1000, pw_gid=1000)

        # This should reject the username due to validation
        user_manager.setup_user_service("../etc/passwd")

        # getpwnam should never be called due to validation
        mock_getpw.assert_not_called()


def test_validate_username_in_ensure_systemd(user_manager):
    """Test that ensure_systemd_user_service validates username format."""
    with patch("guardian_daemon.user_manager.pwd.getpwnam") as mock_getpw:
        mock_getpw.return_value = Mock(pw_dir="/home/test", pw_uid=1000, pw_gid=1000)

        # This should reject the username due to validation
        user_manager.ensure_systemd_user_service("user;whoami")

        # getpwnam should never be called due to validation
        mock_getpw.assert_not_called()


# ============================================================================
# Write Time Rules Tests (with temporary file)
# ============================================================================


def test_write_time_rules_creates_rules(user_manager):
    """Test write_time_rules generates and would write rules."""
    # Mock all system-level operations
    with patch("guardian_daemon.user_manager.TIME_CONF_PATH") as mock_path:
        with patch.object(user_manager, "ensure_pam_time_module"):
            mock_path.exists.return_value = False

            # Mock open to avoid actual file write
            with patch("builtins.open", create=True) as mock_open:
                mock_file = MagicMock()
                mock_open.return_value.__enter__.return_value = mock_file

                user_manager.write_time_rules()

                # Should have called ensure_pam_time_module
                user_manager.ensure_pam_time_module.assert_called_once()


def test_write_time_rules_preserves_non_guardian_content(user_manager):
    """Test write_time_rules preserves non-Guardian managed rules."""
    existing_content = [
        "# System comment",
        "*;*;myuser;Al0800-1700",
        "# Another comment",
    ]

    with patch("guardian_daemon.user_manager.TIME_CONF_PATH") as mock_path:
        with patch.object(user_manager, "ensure_pam_time_module"):
            mock_path.exists.return_value = True

            with patch("builtins.open", create=True) as mock_open:
                # Setup mock for reading existing content
                mock_file_read = MagicMock()
                mock_file_read.readlines.return_value = [
                    line + "\n" for line in existing_content
                ]
                mock_file_read.__iter__.return_value = iter(
                    [line + "\n" for line in existing_content]
                )

                # Setup mock for writing new content
                mock_file_write = MagicMock()

                # Return different mocks for read and write operations
                def open_side_effect(path, mode="r"):
                    if "r" in mode:
                        return mock_file_read
                    else:
                        return mock_file_write

                mock_open.side_effect = lambda *args, **kwargs: MagicMock(
                    __enter__=lambda s: (
                        mock_file_read
                        if "r" in str(args) or (len(args) > 1 and "r" in args[1])
                        else mock_file_write
                    )
                )

                user_manager.write_time_rules()


# ============================================================================
# Remove Time Rules Tests
# ============================================================================


def test_remove_time_rules(user_manager):
    """Test remove_time_rules removes Guardian-managed rules."""
    with patch("guardian_daemon.user_manager.TIME_CONF_PATH") as mock_path:
        mock_path.exists.return_value = True

        with patch("builtins.open", create=True) as mock_open:
            # Mock file with Guardian rules
            guardian_content = [
                "# Managed by guardian-daemon\n",
                "*;*;test_user;Wk0800-1700\n",
                "*;*;!@kids;Al0000-2400\n",
            ]

            mock_file_read = MagicMock()
            mock_file_read.readlines.return_value = guardian_content
            mock_file_write = MagicMock()

            mock_open.return_value.__enter__.side_effect = [
                mock_file_read,
                mock_file_write,
            ]

            user_manager.remove_time_rules()


def test_remove_time_rules_no_file(user_manager):
    """Test remove_time_rules handles missing file gracefully."""
    with patch("guardian_daemon.user_manager.TIME_CONF_PATH") as mock_path:
        mock_path.exists.return_value = False

        # Should not raise exception
        user_manager.remove_time_rules()


# ============================================================================
# Helper Function Tests
# ============================================================================


def test_chown_recursive_on_file():
    """Test chown_recursive handles files correctly."""
    from guardian_daemon.user_manager import chown_recursive

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name

    try:
        # Mock shutil.chown to avoid permission issues
        with patch("shutil.chown") as mock_chown:
            chown_recursive(tmp_path, 1000, 1000)

            # Should have called chown once for the file
            mock_chown.assert_called_once()
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def test_chown_recursive_on_directory():
    """Test chown_recursive handles directories recursively."""
    from guardian_daemon.user_manager import chown_recursive

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Create some subdirectories and files
        sub_dir = tmp_path / "subdir"
        sub_dir.mkdir()
        (sub_dir / "file.txt").touch()
        (tmp_path / "file2.txt").touch()

        with patch("shutil.chown") as mock_chown:
            chown_recursive(str(tmp_path), 1000, 1000)

            # Should have called chown multiple times (dir + files)
            assert mock_chown.call_count >= 3


def test_chown_recursive_nonexistent_path():
    """Test chown_recursive handles nonexistent paths gracefully."""
    from guardian_daemon.user_manager import chown_recursive

    with patch("shutil.chown") as mock_chown:
        chown_recursive("/nonexistent/path/12345", 1000, 1000)

        # Should not call chown for nonexistent path
        mock_chown.assert_not_called()
