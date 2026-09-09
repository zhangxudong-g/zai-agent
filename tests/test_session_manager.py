"""Tests for session manager module."""

import pytest
from pathlib import Path
from zai.session_manager import SessionManager, get_session_manager
from zai.snapshot import Snapshot, save_snapshot


def test_list_sessions_empty(tmp_path, monkeypatch):
    """Test listing sessions when directory is empty."""
    # Patch the saves directory to use tmp_path
    from zai import session_manager as sm_module
    monkeypatch.setattr(sm_module, '_get_saves_dir', lambda: tmp_path)
    
    manager = SessionManager()
    sessions = manager.list_sessions()
    assert sessions == []


def test_list_sessions(tmp_path, monkeypatch):
    """Test listing saved sessions."""
    # Create some snapshot files
    snapshot1 = Snapshot(name="project1")
    snapshot1.add_message("user", "Hello")
    save_snapshot(snapshot1, tmp_path / "project1.json")
    
    snapshot2 = Snapshot(name="project2")
    snapshot2.add_message("user", "Test")
    save_snapshot(snapshot2, tmp_path / "project2.json")
    
    # Patch the saves directory
    from zai import session_manager as sm_module
    monkeypatch.setattr(sm_module, '_get_saves_dir', lambda: tmp_path)
    
    manager = SessionManager()
    sessions = manager.list_sessions()
    
    assert len(sessions) == 2
    names = [s["name"] for s in sessions]
    assert "project1" in names
    assert "project2" in names


def test_save_session_no_agent():
    """Test that save_session without agent raises error."""
    manager = SessionManager(agent=None)
    
    with pytest.raises(RuntimeError) as exc_info:
        manager.save_session("test", agent=None, session_log=None)
    
    assert "Agent not set" in str(exc_info.value)


def test_load_session(tmp_path, monkeypatch):
    """Test loading a saved session."""
    snapshot = Snapshot(name="load-test")
    snapshot.add_message("user", "Test message")
    path = tmp_path / "load-test.json"
    save_snapshot(snapshot, path)
    
    # Patch the saves directory
    from zai import session_manager as sm_module
    monkeypatch.setattr(sm_module, '_get_saves_dir', lambda: tmp_path)
    
    manager = SessionManager()
    loaded = manager.load_session("load-test")
    
    assert loaded["name"] == "load-test"
    assert len(loaded["messages"]) == 1
    assert loaded["messages"][0]["content"] == "Test message"


def test_load_session_not_found(tmp_path, monkeypatch):
    """Test loading a non-existent session raises error."""
    from zai import session_manager as sm_module
    monkeypatch.setattr(sm_module, '_get_saves_dir', lambda: tmp_path)
    
    manager = SessionManager()
    
    with pytest.raises(FileNotFoundError) as exc_info:
        manager.load_session("nonexistent")
    
    assert "Session not found" in str(exc_info.value)


def test_export_session_no_agent():
    """Test that export_session without agent raises error."""
    manager = SessionManager(agent=None)
    
    with pytest.raises((RuntimeError, AttributeError)):
        manager.export_session("test", agent=None, session_log=None)


def test_delete_session(tmp_path, monkeypatch):
    """Test deleting a session."""
    snapshot = Snapshot(name="delete-me")
    save_snapshot(snapshot, tmp_path / "delete-me.json")
    
    from zai import session_manager as sm_module
    monkeypatch.setattr(sm_module, '_get_saves_dir', lambda: tmp_path)
    
    manager = SessionManager()
    assert (tmp_path / "delete-me.json").exists()
    
    result = manager.delete_session("delete-me")
    assert result is True
    assert not (tmp_path / "delete-me.json").exists()


def test_delete_session_not_found(tmp_path, monkeypatch):
    """Test deleting a non-existent session returns False."""
    from zai import session_manager as sm_module
    monkeypatch.setattr(sm_module, '_get_saves_dir', lambda: tmp_path)
    
    manager = SessionManager()
    result = manager.delete_session("nonexistent")
    assert result is False


def test_get_session_manager():
    """Test getting the global session manager instance."""
    sm = get_session_manager()
    assert isinstance(sm, SessionManager)
