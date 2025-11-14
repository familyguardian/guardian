"""
Unit tests for subprocess timeout handling in guardian_daemon.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from guardian_daemon.enforcer import Enforcer
from guardian_daemon.systemd_manager import SystemdManager


@pytest.mark.asyncio
async def test_enforce_terminate_sessions_timeout(test_config):
    """Test that loginctl list-sessions handles timeout correctly."""
    config, _ = test_config

    # Create mock policy and tracker
    mock_policy = MagicMock()
    mock_tracker = MagicMock()
    mock_tracker.session_lock = asyncio.Lock()

    # Create enforcer instance
    enforcer = Enforcer(mock_policy, mock_tracker)

    # Mock create_subprocess_exec to simulate a hanging process
    async def hanging_communicate():
        await asyncio.sleep(20)  # Longer than timeout
        return (b"", b"")

    mock_proc = AsyncMock()
    mock_proc.communicate = hanging_communicate
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        # This should timeout and not hang
        await enforcer.terminate_session("testuser")

        # Verify that kill was called due to timeout
        mock_proc.kill.assert_called_once()
        mock_proc.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_enforce_terminate_session_individual_timeout(test_config):
    """Test that loginctl terminate-session handles timeout correctly."""
    config, _ = test_config

    # Create mock policy
    mock_policy = MagicMock()

    # Create enforcer instance (tracker will be replaced)
    mock_tracker = MagicMock()
    mock_tracker.session_lock = asyncio.Lock()
    enforcer = Enforcer(mock_policy, mock_tracker)

    # Mock the first subprocess call (list-sessions) to return a session
    list_sessions_proc = AsyncMock()
    list_sessions_proc.communicate = AsyncMock(
        return_value=(b"1  123  testuser seat0\n", b"")
    )
    list_sessions_proc.returncode = 0

    # Mock the second subprocess call (terminate-session) to hang
    async def hanging_communicate():
        await asyncio.sleep(20)  # Longer than timeout
        return (b"", b"")

    terminate_proc = AsyncMock()
    terminate_proc.communicate = hanging_communicate
    terminate_proc.kill = MagicMock()
    terminate_proc.wait = AsyncMock()

    call_count = [0]

    async def create_subprocess_side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return list_sessions_proc
        else:
            return terminate_proc

    # Mock active_sessions to return a desktop session
    enforcer.tracker = MagicMock()
    enforcer.tracker.session_lock = asyncio.Lock()
    enforcer.tracker.active_sessions = {
        "1": {"service": "gdm-session", "desktop": "gnome"}
    }

    with patch(
        "asyncio.create_subprocess_exec", side_effect=create_subprocess_side_effect
    ):
        # This should timeout on the terminate call and not hang
        await enforcer.terminate_session("testuser")

        # Verify that kill was called on the hanging process
        terminate_proc.kill.assert_called_once()
        terminate_proc.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_systemd_reload_timeout():
    """Test that systemctl daemon-reload handles timeout correctly."""
    manager = SystemdManager()

    # Mock create_subprocess_exec to simulate a hanging process
    async def hanging_communicate():
        await asyncio.sleep(20)  # Longer than timeout
        return (b"", b"")

    mock_proc = AsyncMock()
    mock_proc.communicate = hanging_communicate
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        # This should timeout and not hang
        await manager.reload_systemd()

        # Verify that kill was called due to timeout
        mock_proc.kill.assert_called_once()
        mock_proc.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_subprocess_success_case():
    """Test that normal subprocess execution still works."""
    manager = SystemdManager()

    # Mock a successful execution
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"success", b""))
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        await manager.reload_systemd()

        # Verify communicate was called (not killed)
        mock_proc.communicate.assert_awaited_once()
        mock_proc.kill.assert_not_called()
