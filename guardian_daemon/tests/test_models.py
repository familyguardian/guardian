"""
Tests for SQLAlchemy models to achieve 100% coverage.
Focuses on __repr__ methods and model instantiation.
"""

import asyncio
import datetime

from guardian_daemon.models import History, Meta, Session, UserSettings
from guardian_daemon.storage import Storage


def test_session_repr(tmp_path):
    """Test Session model __repr__ method."""
    db_path = tmp_path / "test.db"
    storage = Storage(str(db_path))

    # Add a session using the storage API (it's async)
    asyncio.run(
        storage.add_session(
            session_id="session123",
            username="testuser",
            uid=1000,
            start_time=1234567890.0,
            end_time=0.0,
            duration_seconds=0.0,
            desktop="gnome",
            service="gdm",
        )
    )

    # Query the session and test __repr__
    with storage.SessionLocal() as db_session:
        session = db_session.query(Session).first()
        repr_str = repr(session)

        assert "<Session(" in repr_str
        assert "id=" in repr_str
        assert "username=testuser" in repr_str
        assert "logind_session_id=session123" in repr_str
        assert "date=" in repr_str


def test_user_settings_repr(tmp_path):
    """Test UserSettings model __repr__ method."""
    db_path = tmp_path / "test.db"
    storage = Storage(str(db_path))

    # Create a UserSettings entry
    with storage.SessionLocal() as db_session:
        user_setting = UserSettings(username="testuser", settings='{"quota": 60}')
        db_session.add(user_setting)
        db_session.commit()

        repr_str = repr(user_setting)

        assert repr_str == "<UserSettings(username=testuser)>"


def test_meta_repr(tmp_path):
    """Test Meta model __repr__ method."""
    db_path = tmp_path / "test.db"
    storage = Storage(str(db_path))

    # Create a Meta entry
    with storage.SessionLocal() as db_session:
        meta = Meta(key="test_key", value="test_value")
        db_session.add(meta)
        db_session.commit()

        repr_str = repr(meta)

        assert repr_str == "<Meta(key=test_key, value=test_value)>"


def test_history_repr(tmp_path):
    """Test History model __repr__ method."""
    db_path = tmp_path / "test.db"
    storage = Storage(str(db_path))

    # Create a History entry
    with storage.SessionLocal() as db_session:
        history = History(
            username="testuser",
            date="2025-11-10",
            total_screen_time=3600,
            login_count=2,
            first_login="2025-11-10 08:00:00",
            last_logout="2025-11-10 09:00:00",
            quota_exceeded=0,
            bonus_time_used=0,
            created_at="2025-11-10 10:00:00",
        )
        db_session.add(history)
        db_session.commit()

        repr_str = repr(history)

        assert repr_str == "<History(username=testuser, date=2025-11-10)>"


def test_session_model_attributes(tmp_path):
    """Test Session model has all expected attributes after DB roundtrip."""
    db_path = tmp_path / "test.db"
    storage = Storage(str(db_path))

    # Add a complete session
    asyncio.run(
        storage.add_session(
            session_id="session456",
            username="alice",
            uid=1001,
            start_time=1234567890.0,
            end_time=1234571490.0,
            duration_seconds=3600.0,
            desktop="kde",
            service="sddm",
        )
    )

    # Verify all attributes
    with storage.SessionLocal() as db_session:
        session = db_session.query(Session).first()

        assert session.id is not None
        assert session.username == "alice"
        assert session.uid == 1001
        assert isinstance(session.date, datetime.date)
        assert session.logind_session_id == "session456"
        assert session.start_time == 1234567890.0
        assert session.end_time == 1234571490.0
        assert session.duration == 3600.0
        assert session.desktop == "kde"
        assert session.service == "sddm"


def test_history_model_defaults(tmp_path):
    """Test History model default values."""
    db_path = tmp_path / "test.db"
    storage = Storage(str(db_path))

    # Create History with minimal required fields
    with storage.SessionLocal() as db_session:
        history = History(
            username="bob", date="2025-11-10", created_at="2025-11-10 12:00:00"
        )
        db_session.add(history)
        db_session.commit()
        db_session.refresh(history)

        # Check defaults are applied
        assert history.total_screen_time == 0
        assert history.login_count == 0
        assert history.quota_exceeded == 0
        assert history.bonus_time_used == 0
        assert history.first_login is None
        assert history.last_logout is None
