"""
Unit tests for the storage module of guardian_daemon.
"""

from datetime import datetime, timedelta

import pytest

from guardian_daemon.storage import Storage


@pytest.fixture
def storage(test_config):
    """Fixture to provide a storage instance with test database."""
    config, _ = test_config
    return Storage(config["db_path"])


@pytest.mark.asyncio
async def test_storage_init(storage):
    """Test storage initialization."""
    # Check that engine and tables exist
    assert storage.engine is not None
    assert storage.SessionLocal is not None

    # Verify tables were created
    from sqlalchemy import inspect

    inspector = inspect(storage.engine)
    table_names = inspector.get_table_names()
    assert "sessions" in table_names
    assert "user_settings" in table_names
    assert "meta" in table_names
    assert "history" in table_names


@pytest.mark.asyncio
async def test_add_and_get_session(storage):
    """Test adding and retrieving sessions."""
    username = "testuser"
    session_id = "test_session_1"
    start_time = datetime.now()
    duration = 3600  # 1 hour in seconds

    # Add session - active session should have no end time
    await storage.add_session(
        session_id=session_id,
        username=username,
        uid=1000,
        start_time=start_time.timestamp(),
        end_time=0,
        duration_seconds=duration,
    )

    # Get session
    session = await storage.get_active_session(username, session_id)
    assert session is not None
    assert session[0] == username  # SQLite returns a tuple
    assert session[1] == session_id
    assert abs((datetime.fromisoformat(session[2]) - start_time).total_seconds()) < 1


@pytest.mark.asyncio
async def test_end_session(storage):
    """Test ending sessions."""
    username = "testuser"
    session_id = "test_session_1"
    start_time = datetime.now()

    # Add session - active session should have no end time
    duration_seconds = 3600  # 1 hour
    await storage.add_session(
        session_id=session_id,
        username=username,
        uid=1000,
        start_time=start_time.timestamp(),
        end_time=0,  # Active session
        duration_seconds=duration_seconds,
    )

    # End the session
    end_time = start_time + timedelta(hours=1)
    await storage.end_session(username, session_id, end_time)

    # Verify session is no longer active
    session = await storage.get_active_session(username, session_id)
    assert session is None

    # Verify usage time was recorded
    daily_usage = await storage.get_daily_usage(username, start_time.date())
    assert daily_usage > 0


@pytest.mark.asyncio
async def test_get_usage_time(storage):
    """Test retrieving usage time statistics."""
    username = "testuser"
    now = datetime.now()

    # Add a session with 1 hour duration (ended session)
    start_time = now - timedelta(hours=2)
    end_time = start_time + timedelta(hours=1)
    duration_seconds = 3600  # 1 hour in seconds
    await storage.add_session(
        session_id="test_session_1",
        username=username,
        uid=1000,
        start_time=start_time.timestamp(),
        end_time=end_time.timestamp(),
        duration_seconds=duration_seconds,
    )

    # Test daily usage - use the start date of the session
    daily_usage = await storage.get_daily_usage(username, start_time.date())
    assert daily_usage == 3600  # 1 hour in seconds

    # Test weekly usage
    weekly_usage = await storage.get_weekly_usage(username, start_time.date())
    assert weekly_usage == 3600

    # Test getting usage in range
    range_usage = await storage.get_usage_in_date_range(
        username, now - timedelta(days=1), now + timedelta(days=1)
    )
    assert range_usage == 3600


@pytest.mark.asyncio
async def test_cleanup_stale_sessions(storage):
    """Test cleaning up stale sessions."""
    username = "testuser"
    session_id = "test_session_1"
    start_time = datetime.now() - timedelta(days=2)  # Old session

    # Add stale session
    end_time = start_time + timedelta(hours=1)
    duration = 3600
    await storage.add_session(
        session_id=session_id,
        username=username,
        uid=1000,
        start_time=start_time.timestamp(),
        end_time=end_time.timestamp(),
        duration_seconds=duration,
    )

    # Verify it was added
    sessions = storage.get_sessions_for_user(username)
    assert len(sessions) > 0

    # Run cleanup
    await storage.cleanup_stale_sessions(max_age_hours=24)

    # Verify session was cleaned up (shouldn't be any sessions left)
    sessions_after = storage.get_sessions_for_user(username)
    assert len(sessions_after) == 0


@pytest.mark.asyncio
async def test_get_all_active_sessions(storage):
    """Test retrieving all active sessions."""
    # Add multiple active sessions (end_time=0 means active)
    sessions = [
        ("user1", "session1", datetime.now()),
        ("user2", "session2", datetime.now()),
        ("user1", "session3", datetime.now()),
    ]

    for username, session_id, start_time in sessions:
        duration = 3600
        await storage.add_session(
            session_id=session_id,
            username=username,
            uid=1000,
            start_time=start_time.timestamp(),
            end_time=0,  # Active session
            duration_seconds=duration,
        )

    # Get active sessions
    active_sessions = await storage.get_all_active_sessions()
    assert len(active_sessions) == 3

    # End one session
    await storage.end_session("user1", "session1", datetime.now())

    # Verify session count updated
    active_sessions = await storage.get_all_active_sessions()
    assert len(active_sessions) == 2


@pytest.mark.asyncio
async def test_concurrent_database_access(storage):
    """Test that concurrent database operations don't cause locking issues."""
    import asyncio

    username = "concurrent_user"

    # Create multiple concurrent write operations
    async def add_session_task(session_num):
        session_id = f"session_{session_num}"
        start_time = datetime.now()
        await storage.add_session(
            session_id=session_id,
            username=username,
            uid=1000,
            start_time=start_time.timestamp(),
            end_time=0,
            duration_seconds=0,
        )

    # Run 10 concurrent session additions
    tasks = [add_session_task(i) for i in range(10)]
    await asyncio.gather(*tasks)

    # Verify all sessions were added
    sessions = storage.get_sessions_for_user(username)
    assert len(sessions) == 10

    # Test concurrent updates
    async def update_session_task(session_num):
        session_id = f"session_{session_num}"
        storage.update_session_progress(session_id, float(session_num * 60))

    tasks = [update_session_task(i) for i in range(10)]
    await asyncio.gather(*tasks)

    # Verify updates completed successfully
    sessions = storage.get_sessions_for_user(username)
    assert len(sessions) == 10
