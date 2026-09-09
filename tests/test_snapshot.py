"""Tests for snapshot module."""

import pytest
import json
from pathlib import Path
from zai.snapshot import Snapshot, load_snapshot, save_snapshot, snapshot_from_jsonl


def test_snapshot_creation():
    """Test creating a snapshot."""
    snapshot = Snapshot(name="test")
    snapshot.add_message("user", "Hello")
    snapshot.add_message("assistant", "Hi there")
    
    data = snapshot.to_dict()
    assert data["name"] == "test"
    assert data["version"] == "1.0"
    assert len(data["messages"]) == 2
    assert data["messages"][0]["role"] == "user"
    assert data["messages"][0]["content"] == "Hello"


def test_snapshot_add_tool_call():
    """Test adding tool calls to snapshot."""
    snapshot = Snapshot(name="test")
    snapshot.add_tool_call("read", {"path": "test.py"}, "file content")
    
    assert len(snapshot.tool_calls) == 1
    assert snapshot.tool_calls[0]["tool"] == "read"
    assert snapshot.tool_calls[0]["args"]["path"] == "test.py"


def test_snapshot_roundtrip(tmp_path):
    """Test saving and loading a snapshot."""
    snapshot = Snapshot(name="roundtrip-test")
    snapshot.add_message("user", "Test")
    snapshot.config = {"model": "test"}
    
    path = tmp_path / "test.json"
    save_snapshot(snapshot, path)
    
    loaded = load_snapshot(path)
    assert loaded.name == "roundtrip-test"
    assert len(loaded.messages) == 1
    assert loaded.config["model"] == "test"


def test_snapshot_from_jsonl(tmp_path):
    """Test parsing a JSONL log file."""
    jsonl_file = tmp_path / "session.jsonl"
    
    # Create a sample JSONL file
    jsonl_content = json.dumps({"event": "user_message", "content": "Hello"}) + "\n"
    jsonl_content += json.dumps({"event": "result_message", "subtype": "success", "content": "Hi there"}) + "\n"
    jsonl_content += json.dumps({
        "event": "tool_call_end",
        "tool_name": "read",
        "arguments": {"path": "test.py"},
        "result": "file content"
    }) + "\n"
    
    jsonl_file.write_text(jsonl_content)
    
    snapshot = snapshot_from_jsonl(jsonl_file)
    
    assert snapshot.name == "session"
    assert len(snapshot.messages) == 2
    assert snapshot.messages[0]["role"] == "user"
    assert snapshot.messages[1]["role"] == "assistant"
    assert len(snapshot.tool_calls) == 1
    assert snapshot.tool_calls[0]["tool"] == "read"


def test_snapshot_from_dict():
    """Test creating snapshot from dictionary."""
    data = {
        "version": "1.0",
        "name": "from_dict_test",
        "created_at": "2025-01-01T00:00:00Z",
        "config": {"model": "qwen3:1.7b"},
        "messages": [{"role": "user", "content": "Test"}],
        "tool_calls": [],
        "metadata": {"total_tokens": 100},
    }
    
    snapshot = Snapshot.from_dict(data)
    
    assert snapshot.name == "from_dict_test"
    assert snapshot.config["model"] == "qwen3:1.7b"
    assert len(snapshot.messages) == 1
    assert snapshot.metadata["total_tokens"] == 100


def test_snapshot_nonexistent_jsonl(tmp_path):
    """Test parsing a non-existent JSONL file returns empty snapshot."""
    nonexistent = tmp_path / "nonexistent.jsonl"
    snapshot = snapshot_from_jsonl(nonexistent)
    
    assert snapshot.name == "nonexistent"
    assert snapshot.messages == []
    assert snapshot.tool_calls == []
