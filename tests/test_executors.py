"""Tests for tool executors."""

import pytest

from zai.executors import (
    build_concurrent_executor,
    build_sequential_executor,
    get_executor,
)


def test_build_concurrent_executor():
    """Test building concurrent executor."""
    executor = build_concurrent_executor()
    assert executor is not None


def test_build_sequential_executor():
    """Test building sequential executor."""
    executor = build_sequential_executor()
    assert executor is not None


def test_get_executor_sequential():
    """Test getting sequential executor by name."""
    executor = get_executor("sequential")
    assert executor is not None


def test_get_executor_concurrent():
    """Test getting concurrent executor by name."""
    executor = get_executor("concurrent")
    assert executor is not None


def test_get_executor_invalid_mode():
    """Test that invalid mode raises error."""
    with pytest.raises(ValueError) as exc_info:
        get_executor("invalid")

    assert "Unknown executor mode" in str(exc_info.value)


def test_get_executor_case_insensitive():
    """Test that mode matching is case insensitive."""
    executor1 = get_executor("CONCURRENT")
    executor2 = get_executor("concurrent")

    # Both should be executors (we don't compare types deeply)
    assert executor1 is not None
    assert executor2 is not None
