"""Tests for session manager module."""

import pytest
from pathlib import Path
from zai.session_manager import SessionManager, get_session_manager
from zai.snapshot import Snapshot, save_snapshot


def test_list_sessions_empty(tmp_path):
    """Test listing sessions when directory is empty."""
    sm = SessionManager(saves_dir=tmp_path)
    sessions = sm.list_sessions()
    assert sessions == []


def test_list_sessions(tmp_path):
    """Test listing saved sessions."""
    # Create some snapshot files
    snapshot1 = Snapshot(name="project1")
    snapshot1.add_message("user", "Hello")
    save_snapshot(snapshot1, tmp_path / "project1.json")
    
    snapshot2 = Snapshot(name="project2")
    snapshot2.add_message("user", "Test")
    save_snapshot(snapshot2, tmp_path / "project2.json")
    
    sm = SessionManager(saves_dir=tmp_path)
    sessions = sm.list_sessions()
    
    assert len(sessions) == 2
    names = [s["name"] for s in sessions]
    assert "project1" in names
    assert "project2" in names


def test_save_session(tmp_path, tmp_path_factory):
    """Test saving a session."""
    # Create a mock config
    class MockConfig:
        ollama_base_url = "http://localhost:11434"
        ollama_model = "qwen3:1.7b"
        agent_workspace = tmp_path
    
    # Create a mock session log
    session_log = tmp_path / "session.jsonl"
    session_log.write_text('{"event": "user_message", "content": "Hello"}\n')
    
    sm = SessionManager(saves_dir=tmp_path)
    path = sm.save_session("test-session", MockConfig(), session_log)
    
    assert path.exists()
    assert path.name == "test-session.json"
    
    # Verify content
    loaded = sm.load_session("test-session")
    assert loaded.name == "test-session"


def test_load_session(tmp_path):
    """Test loading a saved session."""
    snapshot = Snapshot(name="load-test")
    snapshot.add_message("user", "Test message")
    path = tmp_path / "load-test.json"
    save_snapshot(snapshot, path)
    
    sm = SessionManager(saves_dir=tmp_path)
    loaded = sm.load_session("load-test")
    
    assert loaded.name == "load-test"
    assert len(loaded.messages) == 1
    assert loaded.messages[0]["content"] == "Test message"


def test_load_session_not_found(tmp_path):
    """Test loading a non-existent session raises error."""
    sm = SessionManager(saves_dir=tmp_path)
    
    with pytest.raises(FileNotFoundError) as exc_info:
        sm.load_session("nonexistent")
    
    assert "Session not found" in str(exc_info.value)


def test_export_session(tmp_path):
    """Test exporting a session."""
    session_log = tmp_path / "export.jsonl"
    session_log.write_text('{"event": "user_message", "content": "Export me"}\n')
    
    sm = SessionManager(saves_dir=tmp_path / "saves", exports_dir=tmp_path / "exports")
    path = sm.export_session("exported", session_log)
    
    assert path.exists()
    assert "exported" in path.name
    
    # Verify content
    import json
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["name"] == "exported"


def test_delete_session(tmp_path):
    """Test deleting a session."""
    snapshot = Snapshot(name="delete-me")
    save_snapshot(snapshot, tmp_path / "delete-me.json")
    
    sm = SessionManager(saves_dir=tmp_path)
    assert (tmp_path / "delete-me.json").exists()
    
    result = sm.delete_session("delete-me")
    assert result is True
    assert not (tmp_path / "delete-me.json").exists()


def test_delete_session_not_found(tmp_path):
    """Test deleting a non-existent session returns False."""
    sm = SessionManager(saves_dir=tmp_path)
    result = sm.delete_session("nonexistent")
    assert result is False


def test_get_session_manager():
    """Test getting the global session manager instance."""
    sm = get_session_manager()
    assert isinstance(sm, SessionManager)
    assert sm.saves_dir is not None
    assert sm.exports_dir is not None
