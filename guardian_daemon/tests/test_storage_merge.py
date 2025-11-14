"""
Test that user settings are properly merged with defaults in sync_config_to_db.
"""

import tempfile

from guardian_daemon.storage import Storage


def test_sync_config_merges_defaults_with_user_settings():
    """Test that sync_config_to_db merges user settings with defaults."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=True) as tmp:
        storage = Storage(tmp.name)

        # Config with defaults and a user with partial settings
        config = {
            "defaults": {
                "daily_quota_minutes": 90,
                "weekly_quota_minutes": 600,
                "grace_minutes": 5,
                "bonus_pool_minutes": 30,
                "curfew": {
                    "weekdays": "08:00-20:00",
                    "saturday": "10:00-22:00",
                    "sunday": "10:00-20:00",
                },
            },
            "users": {
                "testuser": {
                    "daily_quota_minutes": 120,  # Override daily quota
                    "curfew": {"weekdays": "09:00-21:00"},  # Override weekday curfew
                },
                "emptyuser": {},  # Should get all defaults
            },
        }

        storage.sync_config_to_db(config)

        # Check testuser - should have merged settings
        testuser_settings = storage.get_user_settings("testuser")
        assert testuser_settings is not None
        assert testuser_settings["daily_quota_minutes"] == 120  # Override
        assert testuser_settings["weekly_quota_minutes"] == 600  # From defaults
        assert testuser_settings["grace_minutes"] == 5  # From defaults
        assert testuser_settings["bonus_pool_minutes"] == 30  # From defaults
        assert testuser_settings["curfew"]["weekdays"] == "09:00-21:00"  # Override
        assert testuser_settings["curfew"]["saturday"] == "10:00-22:00"  # From defaults
        assert testuser_settings["curfew"]["sunday"] == "10:00-20:00"  # From defaults

        # Check emptyuser - should have all defaults
        emptyuser_settings = storage.get_user_settings("emptyuser")
        assert emptyuser_settings is not None
        assert emptyuser_settings["daily_quota_minutes"] == 90
        assert emptyuser_settings["weekly_quota_minutes"] == 600
        assert emptyuser_settings["grace_minutes"] == 5
        assert emptyuser_settings["bonus_pool_minutes"] == 30
        assert emptyuser_settings["curfew"]["weekdays"] == "08:00-20:00"
        assert emptyuser_settings["curfew"]["saturday"] == "10:00-22:00"
        assert emptyuser_settings["curfew"]["sunday"] == "10:00-20:00"

        storage.close()


def test_sync_config_stores_defaults():
    """Test that sync_config_to_db properly stores defaults."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=True) as tmp:
        storage = Storage(tmp.name)

        config = {
            "defaults": {"daily_quota_minutes": 90, "grace_minutes": 5},
            "users": {},
        }

        storage.sync_config_to_db(config)

        # Check defaults are stored
        defaults = storage.get_user_settings("default")
        assert defaults is not None
        assert defaults["daily_quota_minutes"] == 90
        assert defaults["grace_minutes"] == 5

        storage.close()
