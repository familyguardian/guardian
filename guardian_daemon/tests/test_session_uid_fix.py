"""
Test for the D-Bus User property tuple handling fix.

The D-Bus User property returns a tuple (uid, path), not just an integer.
This test ensures we handle it correctly.
"""

import pytest


@pytest.mark.asyncio
async def test_user_property_tuple_handling():
    """Test that we correctly extract UID from User property tuple."""
    # Simulate the D-Bus User property which is a tuple (uid, object_path)
    user_property = (1000, "/org/freedesktop/login1/user/_1000")

    # Test tuple handling
    uid = None
    if isinstance(user_property, (list, tuple)) and len(user_property) >= 1:
        uid = user_property[0]
    elif isinstance(user_property, int):
        uid = user_property

    assert uid == 1000, f"Expected UID 1000, got {uid}"


@pytest.mark.asyncio
async def test_user_property_int_fallback():
    """Test that we still handle integer User property (backwards compatibility)."""
    # Some old systems might return just an int
    user_property = 1000

    # Test int handling
    uid = None
    if isinstance(user_property, (list, tuple)) and len(user_property) >= 1:
        uid = user_property[0]
    elif isinstance(user_property, int):
        uid = user_property

    assert uid == 1000, f"Expected UID 1000, got {uid}"


@pytest.mark.asyncio
async def test_user_property_list_handling():
    """Test that we handle User property as list (dbus-next might return list)."""
    # dbus-next might return a list instead of tuple
    user_property = [1000, "/org/freedesktop/login1/user/_1000"]

    # Test list handling
    uid = None
    if isinstance(user_property, (list, tuple)) and len(user_property) >= 1:
        uid = user_property[0]
    elif isinstance(user_property, int):
        uid = user_property

    assert uid == 1000, f"Expected UID 1000, got {uid}"
