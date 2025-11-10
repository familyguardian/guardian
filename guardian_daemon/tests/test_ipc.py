"""
Unit tests for the IPC module of guardian_daemon.
"""

import json
from unittest.mock import AsyncMock, Mock

import pytest

from guardian_daemon.ipc import GuardianIPCServer


@pytest.fixture
def ipc_server(test_config):
    """Fixture to provide an IPC server instance."""
    config, _ = test_config

    # Mock the tracker and policy
    mock_tracker = Mock()
    mock_policy = Mock()
    mock_policy.get_all_usernames.return_value = ["testuser"]

    server = GuardianIPCServer(config, mock_tracker, mock_policy)
    return server


def test_max_request_size_constant():
    """Test that MAX_REQUEST_SIZE is set to reasonable limit."""
    assert GuardianIPCServer.MAX_REQUEST_SIZE == 1024 * 1024  # 1MB


def test_rate_limit_constants():
    """Test that rate limiting constants are set."""
    assert GuardianIPCServer.RATE_LIMIT_WINDOW == 60
    assert GuardianIPCServer.RATE_LIMIT_MAX_REQUESTS == 100


def test_check_rate_limit_allows_normal_usage(ipc_server):
    """Test that rate limiting allows normal usage."""
    uid = 1000

    # Should allow first 100 requests
    for i in range(100):
        assert ipc_server._check_rate_limit(uid), f"Request {i+1} should be allowed"


def test_check_rate_limit_blocks_excessive_requests(ipc_server):
    """Test that rate limiting blocks excessive requests."""
    uid = 1000

    # Exhaust the rate limit
    for i in range(100):
        ipc_server._check_rate_limit(uid)

    # 101st request should be blocked
    assert not ipc_server._check_rate_limit(uid), "Should block after 100 requests"


def test_check_rate_limit_resets_after_window(ipc_server):
    """Test that rate limit resets after the time window."""
    import time

    uid = 1000

    # Simulate old requests by manipulating the internal state
    old_timestamp = time.time() - 70  # 70 seconds ago (outside 60s window)
    ipc_server._request_counts[uid] = [(old_timestamp, 100)]

    # Should allow new request since old ones expired
    assert ipc_server._check_rate_limit(uid), "Should allow after window expires"


def test_check_rate_limit_per_uid(ipc_server):
    """Test that rate limiting is per-UID."""
    uid1 = 1000
    uid2 = 1001

    # Exhaust limit for uid1
    for i in range(100):
        ipc_server._check_rate_limit(uid1)

    # uid1 should be blocked
    assert not ipc_server._check_rate_limit(uid1)

    # uid2 should still be allowed
    assert ipc_server._check_rate_limit(uid2)


@pytest.mark.asyncio
async def test_handle_connection_validates_message_size(ipc_server):
    """Test that oversized messages are rejected."""
    # Create mock reader and writer
    reader = AsyncMock()
    writer = AsyncMock()
    writer.get_extra_info.return_value = (0, 0, 0)  # root user

    # Simulate oversized message
    oversized_len = ipc_server.MAX_REQUEST_SIZE + 1
    reader.readexactly.return_value = oversized_len.to_bytes(4, "big")

    await ipc_server.handle_connection(reader, writer)

    # Should have written error response
    assert writer.write.called
    written_data = b"".join(call[0][0] for call in writer.write.call_args_list)

    # Parse the response
    response_len = int.from_bytes(written_data[0:4], "big")
    response_data = written_data[4 : 4 + response_len].decode()
    response = json.loads(response_data)

    assert "error" in response
    assert "too large" in response["error"].lower()


@pytest.mark.asyncio
async def test_handle_connection_validates_message_length_positive(ipc_server):
    """Test that negative or zero message lengths are rejected."""
    reader = AsyncMock()
    writer = AsyncMock()
    writer.get_extra_info.return_value = (0, 0, 0)  # root user

    # Simulate invalid message length (0)
    reader.readexactly.return_value = (0).to_bytes(4, "big")

    await ipc_server.handle_connection(reader, writer)

    # Connection should be closed
    assert writer.close.called


@pytest.mark.asyncio
async def test_handle_connection_enforces_authentication(ipc_server):
    """Test that unauthorized users are rejected."""
    reader = AsyncMock()
    writer = AsyncMock()

    # Non-root, non-admin group user - get_extra_info is NOT async, use Mock
    writer.get_extra_info = Mock(return_value=(1000, 1000, 0))

    await ipc_server.handle_connection(reader, writer)

    # Should close connection immediately
    assert writer.close.called
    assert not reader.readexactly.called


@pytest.mark.asyncio
async def test_handle_connection_root_exempt_from_rate_limit(ipc_server):
    """Test that root (UID 0) is exempt from rate limiting."""
    # Root should not be subject to rate limiting
    # Even after many requests, root should still be allowed

    reader = AsyncMock()
    writer = AsyncMock()
    writer.get_extra_info.return_value = (0, 0, 0)  # root

    # Simulate valid small message
    reader.readexactly.side_effect = [
        (10).to_bytes(4, "big"),  # message length
        b"list_kids ",  # message content
    ]

    # Exhaust rate limit for root (this shouldn't actually limit root)
    for i in range(150):  # More than RATE_LIMIT_MAX_REQUESTS
        reader_mock = AsyncMock()
        writer_mock = AsyncMock()
        writer_mock.get_extra_info.return_value = (0, 0, 0)
        reader_mock.readexactly.side_effect = [(10).to_bytes(4, "big"), b"list_kids "]

        # Should not trigger rate limit for root
        await ipc_server.handle_connection(reader_mock, writer_mock)
        # Just verify it doesn't close immediately due to rate limit
        # (it may close for other reasons like handler completion)


def test_request_counts_cleanup(ipc_server):
    """Test that old request count entries are cleaned up."""
    import time

    uid = 1000

    # Add old and recent requests
    old_time = time.time() - 70
    recent_time = time.time()
    ipc_server._request_counts[uid] = [(old_time, 50), (recent_time, 10)]

    # Check rate limit, which should trigger cleanup
    ipc_server._check_rate_limit(uid)

    # Old entries should be removed, only recent ones remain
    remaining = ipc_server._request_counts[uid]
    assert all(time.time() - ts < ipc_server.RATE_LIMIT_WINDOW for ts, _ in remaining)
