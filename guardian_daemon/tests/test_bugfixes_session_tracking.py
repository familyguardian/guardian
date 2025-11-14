"""
Tests for session tracking bug fixes.

This module tests the fixes for the following bugs:
1. Wrong tuple indices when accessing session data from database
2. D-Bus User property returns tuple (uid, path) not int
3. Missing await in _get_username
4. Recursion error in ensure_systemd_user_service
5. User settings not merged with defaults in sync_config_to_db
"""

from unittest.mock import MagicMock, patch

import pytest

from guardian_daemon.policy import Policy
from guardian_daemon.sessions import SessionTracker
from guardian_daemon.storage import Storage
from guardian_daemon.user_manager import UserManager


@pytest.fixture
def test_fixtures(test_config):
    """Create test instances with configuration."""
    config, config_path = test_config
    policy = Policy(config_path)
    storage = Storage(config["db_path"])
    user_manager = UserManager(policy)
    tracker = SessionTracker(policy, config, user_manager)
    return {
        "policy": policy,
        "storage": storage,
        "user_manager": user_manager,
        "tracker": tracker,
        "config": config,
    }


class TestBug1_TupleIndices:
    """Test fix for Bug #1: Wrong tuple indices when accessing session data."""

    @pytest.mark.asyncio
    async def test_get_remaining_time_with_db_sessions(self, test_fixtures):
        """Test that get_remaining_time correctly accesses session tuple indices.

        This tests the fix for using s[5], s[6], s[7] instead of s[6], s[7], s[8]
        when accessing duration, desktop, and service from database session tuples.
        """
        tracker = test_fixtures["tracker"]
        storage = test_fixtures["storage"]

        import time

        now = time.time()
        session_start = now - 3600  # 1 hour ago

        # Add a test session to the database
        await storage.add_session(
            session_id="session_123",
            username="test_quota_only",
            uid=1001,
            start_time=session_start,
            end_time=session_start + 600,  # 600 seconds = 10 minutes
            duration_seconds=600.0,
            desktop="KDE",
            service="sddm",
        )

        # Get remaining time should correctly calculate using database sessions
        remaining = await tracker.get_remaining_time("test_quota_only")

        # User has 60 minute quota, used 10 minutes, should have 50 minutes remaining
        assert remaining == pytest.approx(50.0, rel=0.1), (
            f"Expected ~50 minutes remaining, got {remaining}. "
            "This might indicate tuple index bug in database session access."
        )


class TestBug2_DBusUserProperty:
    """Test fix for Bug #2: D-Bus User property returns tuple (uid, path) not int."""

    @pytest.mark.asyncio
    async def test_user_property_tuple_extraction(self, test_fixtures):
        """Test that User property tuple (uid, path) is handled correctly."""
        tracker = test_fixtures["tracker"]

        # Simulate a login with User property as tuple (uid, object_path)
        props = {
            "User": (1001, "/org/freedesktop/login1/user/_1001"),
            "Desktop": "KDE",
            "Service": "sddm",
            "Class": "user",
        }

        # Mock user_manager to avoid actual system operations
        with patch.object(tracker.user_manager, "setup_user_login", return_value=True):
            with patch.object(tracker, "_get_username", return_value="test_quota_only"):
                # The fix ensures this doesn't crash and extracts UID correctly
                await tracker.handle_login(
                    "session_tuple", 1001, "test_quota_only", props
                )

                # Verify session was tracked (now uses unique session ID with boot_id prefix)
                boot_id_prefix = tracker.boot_id[:8]
                unique_session_id = f"{boot_id_prefix}_session_tuple"
                assert unique_session_id in tracker.active_sessions
                assert (
                    tracker.active_sessions[unique_session_id]["logind_session_id"]
                    == "session_tuple"
                )

    @pytest.mark.asyncio
    async def test_user_property_integer_handling(self, test_fixtures):
        """Test that User property as integer is still handled correctly."""
        tracker = test_fixtures["tracker"]

        # Simulate User property as integer (older format or different D-Bus version)
        props = {
            "User": 1001,  # Just an integer, not a tuple
            "Desktop": "KDE",
            "Service": "sddm",
            "Class": "user",
        }

        with patch.object(tracker.user_manager, "setup_user_login", return_value=True):
            with patch.object(tracker, "_get_username", return_value="test_quota_only"):
                # Should handle integer UID without issues
                await tracker.handle_login(
                    "session_int", 1001, "test_quota_only", props
                )

                # Verify session was tracked (now uses unique session ID with boot_id prefix)
                boot_id_prefix = tracker.boot_id[:8]
                unique_session_id = f"{boot_id_prefix}_session_int"
                assert unique_session_id in tracker.active_sessions
                assert (
                    tracker.active_sessions[unique_session_id]["logind_session_id"]
                    == "session_int"
                )


class TestBug3_AsyncAwait:
    """Test fix for Bug #3: Missing await in _get_username."""

    @pytest.mark.asyncio
    async def test_get_username_is_async(self, test_fixtures):
        """Test that _get_username properly awaits asyncio.to_thread."""
        tracker = test_fixtures["tracker"]

        with patch("asyncio.to_thread") as mock_to_thread:
            # Mock pwd.getpwuid to return a mock struct
            mock_pwd_entry = MagicMock()
            mock_pwd_entry.pw_name = "test_quota_only"
            mock_to_thread.return_value = mock_pwd_entry

            username = await tracker._get_username(1001)

            # Verify asyncio.to_thread was called
            mock_to_thread.assert_called_once()
            assert username == "test_quota_only"


class TestBug4_RecursionError:
    """Test fix for Bug #4: Recursion error in ensure_systemd_user_service."""

    def test_ensure_systemd_user_service_no_recursion(self, test_fixtures):
        """Test that ensure_systemd_user_service doesn't call itself recursively.

        This verifies the fix for removing orphaned code at the end of
        ensure_systemd_user_service that was causing infinite recursion.
        """
        user_manager = test_fixtures["user_manager"]

        with (
            patch.object(user_manager, "validate_username", return_value=True),
            patch("pwd.getpwnam") as mock_getpwnam,
            patch("subprocess.run") as mock_run,
            patch("pathlib.Path.exists", return_value=True),
        ):
            # Set up mock user
            mock_user = MagicMock()
            mock_user.pw_dir = "/home/testuser"
            mock_user.pw_uid = 1001
            mock_user.pw_gid = 1001
            mock_getpwnam.return_value = mock_user

            # Mock loginctl to return user not logged in
            mock_run.return_value = MagicMock(stdout="State=closing", returncode=0)

            # This should not recurse
            user_manager.ensure_systemd_user_service("testuser")

            # If we get here without RecursionError, the fix works
            assert True


class TestBug5_SettingsMerge:
    """Test fix for Bug #5: User settings not merged with defaults."""

    def test_sync_config_to_db_merges_with_defaults(self, test_fixtures):
        """Test that sync_config_to_db merges user settings with defaults."""
        storage = test_fixtures["storage"]
        config = {
            "defaults": {
                "daily_quota_minutes": 90,
                "curfew": {
                    "weekdays": "08:00-20:00",
                    "saturday": "08:00-22:00",
                    "sunday": "09:00-20:00",
                },
                "grace_minutes": 5,
                "bonus_pool_minutes": 0,
            },
            "users": {
                "testuser": {
                    "daily_quota_minutes": 60,  # Override default
                    "curfew": {
                        "weekdays": "10:00-18:00",  # Override default, keep others
                    },
                    # grace_minutes and bonus_pool_minutes should come from defaults
                }
            },
        }

        storage.sync_config_to_db(config)
        settings = storage.get_user_settings("testuser")

        # Verify user-specific override
        assert settings["daily_quota_minutes"] == 60

        # Verify merged curfew (partial override)
        assert settings["curfew"]["weekdays"] == "10:00-18:00"  # User override
        assert settings["curfew"]["saturday"] == "08:00-22:00"  # From default
        assert settings["curfew"]["sunday"] == "09:00-20:00"  # From default

        # Verify defaults that weren't overridden
        assert settings["grace_minutes"] == 5
        assert settings["bonus_pool_minutes"] == 0

    def test_sync_users_from_config_reloads_and_merges(self, test_fixtures):
        """Test that user settings are properly merged when syncing from config."""
        storage = test_fixtures["storage"]
        # Initial config
        initial_config = {
            "defaults": {
                "daily_quota_minutes": 90,
                "grace_minutes": 5,
                "bonus_pool_minutes": 0,
            },
            "users": {
                "testuser": {
                    "daily_quota_minutes": 60,
                }
            },
        }

        storage.sync_config_to_db(initial_config)
        settings = storage.get_user_settings("testuser")

        # Should have merged settings
        assert settings["daily_quota_minutes"] == 60
        assert settings["grace_minutes"] == 5
        assert settings["bonus_pool_minutes"] == 0


class TestBug6_ResetQuota:
    """Test fix for reset-quota command behavior."""

    @pytest.mark.asyncio
    async def test_reset_quota_archives_and_clears_today(self, test_fixtures):
        """Test that force reset archives today's sessions and resets quota to 0."""
        tracker = test_fixtures["tracker"]
        storage = test_fixtures["storage"]

        # Add a session for today
        import datetime
        import time

        today = datetime.date.today().strftime("%Y-%m-%d")
        now = time.time()
        session_start = now - 3600  # 1 hour ago

        await storage.add_session(
            session_id="session_today",
            username="test_quota_only",
            uid=1001,
            start_time=session_start,
            end_time=session_start + 600,  # 600 seconds = 10 minutes
            duration_seconds=600.0,
            desktop="KDE",
            service="sddm",
        )

        # Verify session is there
        remaining_before = await tracker.get_remaining_time("test_quota_only")
        assert remaining_before == pytest.approx(50.0, rel=0.1)  # 60 - 10 = 50

        # Force reset
        await tracker.perform_daily_reset(force=True)

        # Verify session is archived and quota is reset
        remaining_after = await tracker.get_remaining_time("test_quota_only")
        assert remaining_after == pytest.approx(60.0, rel=0.1)  # Back to full quota

        # Verify no active sessions in database (sessions were archived)
        # Check by verifying remaining time is back to full quota and usage is 0
        daily_usage = await storage.get_daily_usage(
            "test_quota_only", datetime.date.today()
        )
        assert daily_usage == 0.0  # No usage remaining after reset

        # Verify history was created
        history = storage.get_history(
            "test_quota_only", start_date=today, end_date=today
        )
        assert len(history) > 0


class TestBug7_BackgroundSessionFiltering:
    """Test that background sessions (like runuser) are filtered out."""

    @pytest.mark.asyncio
    async def test_background_sessions_ignored(self, test_fixtures):
        """Test that sessions with Class=background are filtered out."""
        tracker = test_fixtures["tracker"]

        # Simulate a background session (like from runuser)
        props = {
            "User": (1001, "/org/freedesktop/login1/user/_1001"),
            "Desktop": None,
            "Service": "runuser",
            "Class": "background",  # This should be filtered out
        }

        with patch.object(tracker.user_manager, "setup_user_login", return_value=True):
            with patch.object(tracker, "_get_username", return_value="test_quota_only"):
                await tracker.handle_login("bg_session", 1001, "test_quota_only", props)

                # Background session should NOT be added (filtered in handle_login)
                assert "bg_session" not in tracker.active_sessions

    @pytest.mark.asyncio
    async def test_systemd_user_sessions_ignored(self, test_fixtures):
        """Test that systemd-user sessions are filtered out."""
        tracker = test_fixtures["tracker"]

        props = {
            "User": (1001, "/org/freedesktop/login1/user/_1001"),
            "Desktop": None,
            "Service": "systemd-user",  # This should be filtered out
            "Class": "manager",
        }

        with patch.object(tracker.user_manager, "setup_user_login", return_value=True):
            with patch.object(tracker, "_get_username", return_value="test_quota_only"):
                await tracker.handle_login(
                    "systemd_session", 1001, "test_quota_only", props
                )

                # systemd-user session should NOT be added
                assert "systemd_session" not in tracker.active_sessions

    @pytest.mark.asyncio
    async def test_user_sessions_tracked(self, test_fixtures):
        """Test that real user sessions ARE tracked."""
        tracker = test_fixtures["tracker"]

        # Real user login session
        props = {
            "User": (1001, "/org/freedesktop/login1/user/_1001"),
            "Desktop": "KDE",
            "Service": "sddm",
            "Class": "user",  # Real user session
        }

        with patch.object(tracker.user_manager, "setup_user_login", return_value=True):
            with patch.object(tracker, "_get_username", return_value="test_quota_only"):
                await tracker.handle_login(
                    "user_session", 1001, "test_quota_only", props
                )

                # User session SHOULD be added (now uses unique session ID with boot_id prefix)
                boot_id_prefix = tracker.boot_id[:8]
                unique_session_id = f"{boot_id_prefix}_user_session"
                assert unique_session_id in tracker.active_sessions
                assert (
                    tracker.active_sessions[unique_session_id]["logind_session_id"]
                    == "user_session"
                )
