"""
Unit tests for setup error handling in guardian_daemon.
"""

import pytest
import subprocess
from unittest.mock import patch, MagicMock
from pathlib import Path

from guardian_daemon.user_manager import UserManager, SetupError


@pytest.fixture
def mock_policy():
    """Create a mock policy object."""
    policy = MagicMock()
    policy.data = {
        "users": {
            "testuser": {
                "quota": {"daily": 120},
                "monitored": True
            }
        }
    }
    return policy


def test_setup_error_on_group_creation_failure(mock_policy):
    """Test that group creation failure raises SetupError."""
    manager = UserManager(mock_policy)
    
    # Mock grp.getgrnam to raise KeyError (group doesn't exist)
    with patch('grp.getgrnam', side_effect=KeyError("Group not found")):
        # Mock subprocess.run to fail
        with patch('subprocess.run', side_effect=subprocess.CalledProcessError(1, 'groupadd', stderr="Permission denied")):
            with pytest.raises(SetupError) as exc_info:
                manager.ensure_kids_group()
            
            assert "Failed to create group 'kids'" in str(exc_info.value)


def test_setup_error_on_group_creation_timeout(mock_policy):
    """Test that group creation timeout raises SetupError."""
    manager = UserManager(mock_policy)
    
    # Mock grp.getgrnam to raise KeyError (group doesn't exist)
    with patch('grp.getgrnam', side_effect=KeyError("Group not found")):
        # Mock subprocess.run to timeout
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('groupadd', 5)):
            with pytest.raises(SetupError) as exc_info:
                manager.ensure_kids_group()
            
            assert "timed out" in str(exc_info.value)


def test_setup_error_on_missing_authselect(mock_policy):
    """Test that missing authselect raises SetupError."""
    manager = UserManager(mock_policy)
    
    # Mock the _ensure_sddm_pam_time to succeed (we're only testing authselect)
    with patch.object(manager, '_ensure_sddm_pam_time'):
        # Mock shutil.which to return None (authselect not found)
        with patch('shutil.which', return_value=None):
            with pytest.raises(SetupError) as exc_info:
                manager.ensure_pam_time_module()
            
            assert "authselect command not found" in str(exc_info.value)


def test_setup_error_exception_class():
    """Test that SetupError is a proper exception class."""
    # Create exception
    exc = SetupError("Test error message")
    
    # Verify it's an Exception
    assert isinstance(exc, Exception)
    
    # Verify message
    assert str(exc) == "Test error message"
    
    # Verify it can be raised and caught
    with pytest.raises(SetupError) as exc_info:
        raise SetupError("Another test error")
    
    assert "Another test error" in str(exc_info.value)


def test_no_setup_error_when_group_exists(mock_policy):
    """Test that no error is raised when group already exists."""
    manager = UserManager(mock_policy)
    
    # Mock grp.getgrnam to succeed (group exists)
    mock_group = MagicMock()
    mock_group.gr_gid = 1000
    with patch('grp.getgrnam', return_value=mock_group):
        # Mock user_exists and other dependencies
        with patch.object(manager, 'user_exists', return_value=True):
            with patch('grp.getgrall', return_value=[mock_group]):
                with patch('subprocess.run'):
                    # Should not raise
                    manager.ensure_kids_group()


def test_setup_error_propagation_to_main():
    """Test that SetupError is properly caught in main entry point."""
    from guardian_daemon.__main__ import main
    
    # Mock Config to succeed
    with patch('guardian_daemon.__main__.Config'):
        # Mock setup_logging to succeed
        with patch('guardian_daemon.__main__.setup_logging'):
            # Mock GuardianDaemon to succeed initialization
            with patch('guardian_daemon.__main__.GuardianDaemon') as mock_daemon_class:
                # Mock asyncio.run to raise SetupError
                with patch('asyncio.run', side_effect=SetupError("Test setup failure")):
                    # Should exit with code 1
                    with pytest.raises(SystemExit) as exc_info:
                        main()
                    
                    assert exc_info.value.code == 1
