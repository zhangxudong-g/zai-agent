"""Tests for session manager module."""

import json

import pytest

from zai.session_manager import SessionManager, get_session_manager
from zai.snapshot import Snapshot, save_snapshot


def test_list_sessions_empty(tmp_path, monkeypatch):
    """Test listing sessions when directory is empty."""
    # Patch the saves directory to use tmp_path
    from zai import session_manager as sm_module

    monkeypatch.setattr(sm_module, "_get_saves_dir", lambda: tmp_path)

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

    monkeypatch.setattr(sm_module, "_get_saves_dir", lambda: tmp_path)

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

    monkeypatch.setattr(sm_module, "_get_saves_dir", lambda: tmp_path)

    manager = SessionManager()
    loaded = manager.load_session("load-test")

    assert loaded["name"] == "load-test"
    assert len(loaded["messages"]) == 1
    assert loaded["messages"][0]["content"] == "Test message"


def test_load_session_not_found(tmp_path, monkeypatch):
    """Test loading a non-existent session raises error."""
    from zai import session_manager as sm_module

    monkeypatch.setattr(sm_module, "_get_saves_dir", lambda: tmp_path)

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

    monkeypatch.setattr(sm_module, "_get_saves_dir", lambda: tmp_path)

    manager = SessionManager()
    assert (tmp_path / "delete-me.json").exists()

    result = manager.delete_session("delete-me")
    assert result is True
    assert not (tmp_path / "delete-me.json").exists()


def test_delete_session_not_found(tmp_path, monkeypatch):
    """Test deleting a non-existent session returns False."""
    from zai import session_manager as sm_module

    monkeypatch.setattr(sm_module, "_get_saves_dir", lambda: tmp_path)

    manager = SessionManager()
    result = manager.delete_session("nonexistent")
    assert result is False


def test_get_session_manager():
    """Test getting the global session manager instance."""
    sm = get_session_manager()
    assert isinstance(sm, SessionManager)


def test_extract_messages_from_dict_messages():
    """Test _extract_messages handles dict messages correctly."""
    from zai.session_manager import _extract_messages

    class FakeAgent:
        pass

    fake = FakeAgent()
    fake._inner = type("Inner", (), {})()
    fake._inner.messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"},
    ]

    messages = _extract_messages(fake)
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Hello"
    assert messages[1]["role"] == "assistant"


def test_extract_messages_from_list_content():
    """Test _extract_messages handles list content blocks."""
    from zai.session_manager import _extract_messages

    class FakeAgent:
        pass

    fake = FakeAgent()
    fake._inner = type("Inner", (), {})()
    fake._inner.messages = [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "Block 1"}, {"type": "text", "text": "Block 2"}],
        }
    ]

    messages = _extract_messages(fake)
    assert len(messages) == 1
    assert "Block 1" in messages[0]["content"]
    assert "Block 2" in messages[0]["content"]


def test_extract_messages_no_inner():
    """Test _extract_messages handles agent without _inner."""
    from zai.session_manager import _extract_messages

    class FakeAgent:
        messages = [{"role": "user", "content": "Direct"}]  # noqa: RUF012

    fake = FakeAgent()
    messages = _extract_messages(fake)
    assert len(messages) == 1
    assert messages[0]["content"] == "Direct"


def test_extract_messages_empty():
    """Test _extract_messages handles no messages."""
    from zai.session_manager import _extract_messages

    class FakeAgent:
        pass

    fake = FakeAgent()
    fake._inner = type("Inner", (), {})()
    fake._inner.messages = []

    messages = _extract_messages(fake)
    assert messages == []


def test_save_session_with_wrapper_agent(tmp_path, monkeypatch):
    """Test save_session works with zai wrapper agent (no _inner.messages)."""
    from zai import session_manager as sm_module

    monkeypatch.setattr(sm_module, "_get_saves_dir", lambda: tmp_path)

    # Create a fake agent that mimics our zai.agent.Agent wrapper
    class FakeConfig:
        ollama_model = "qwen3:1.7b"
        agent_workspace = "/tmp"

    class FakeInner:
        messages = [{"role": "user", "content": "test"}]  # noqa: RUF012

    class FakeAgent:
        def __init__(self):
            self.config = FakeConfig()
            self._inner = FakeInner()

    agent = FakeAgent()
    manager = SessionManager(agent=agent)

    path = manager.save_session("wrapper-test", agent)

    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["name"] == "wrapper-test"
    assert len(data["messages"]) == 1
    assert data["messages"][0]["content"] == "test"


def test_export_session_with_wrapper_agent(tmp_path, monkeypatch):
    """Test export_session works with zai wrapper agent."""
    from zai import session_manager as sm_module

    monkeypatch.setattr(sm_module, "_get_exports_dir", lambda: tmp_path)

    class FakeConfig:
        ollama_model = "qwen3:1.7b"
        agent_workspace = "/tmp"

    class FakeInner:
        messages = [{"role": "user", "content": "export me"}]  # noqa: RUF012

    class FakeAgent:
        def __init__(self):
            self.config = FakeConfig()
            self._inner = FakeInner()

    agent = FakeAgent()
    manager = SessionManager(agent=agent)

    path = manager.export_session("export-test", agent)

    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["name"] == "export-test"


def test_restore_session_injects_messages(tmp_path, monkeypatch):
    """Test restore_session actually injects messages into agent._inner."""
    from zai import session_manager as sm_module

    monkeypatch.setattr(sm_module, "_get_saves_dir", lambda: tmp_path)

    # First save a session with messages
    class FakeConfig:
        ollama_model = "qwen3:1.7b"
        agent_workspace = "/tmp"

    class FakeInner:
        def __init__(self):
            self.messages = [
                {"role": "user", "content": "我叫7c"},
                {"role": "assistant", "content": "好的,7c"},
            ]

    class FakeAgent:
        def __init__(self):
            self.config = FakeConfig()
            self._inner = FakeInner()

    save_agent = FakeAgent()
    manager = SessionManager(agent=save_agent)
    manager.save_session("name-test", save_agent)

    # Now create a fresh agent with NO messages and restore into it
    class FreshInner:
        def __init__(self):
            self.messages = []

    class FreshAgent:
        def __init__(self):
            self.config = FakeConfig()
            self._inner = FreshInner()

    fresh = FreshAgent()
    restored = manager.restore_session("name-test", fresh)

    assert restored == 2
    # The fresh agent now has the restored messages in Strands format
    assert len(fresh._inner.messages) == 2
    # Verify messages are in Strands block format
    msg0 = fresh._inner.messages[0]
    assert msg0["role"] == "user"
    # content should now be a list of blocks
    assert isinstance(msg0["content"], list)
    assert msg0["content"][0]["text"] == "我叫7c"


def test_restore_session_empty():
    """Test restore_session on empty session returns 0."""
    import json

    class FakeAgent:
        _inner = type("I", (), {})()

    agent = FakeAgent()
    manager = SessionManager(agent=agent)

    # Create an empty snapshot manually
    from pathlib import Path

    saves_dir = Path("_test_saves")
    saves_dir.mkdir(exist_ok=True)
    try:
        (saves_dir / "empty.json").write_text(
            json.dumps({"name": "empty", "messages": []}), encoding="utf-8"
        )
        # Point the manager at our dir
        manager._saves_dir = saves_dir
        restored = manager.restore_session("empty", agent)
        assert restored == 0
    finally:
        for p in saves_dir.glob("*.json"):
            p.unlink()
        saves_dir.rmdir()


def test_save_session_graceful_failure_on_strands_error(tmp_path, monkeypatch):
    """Test that Strands SnapshotSessionManager errors don't break save_session."""
    from zai import session_manager as sm_module

    monkeypatch.setattr(sm_module, "_get_saves_dir", lambda: tmp_path)

    class FakeConfig:
        ollama_model = "test"
        agent_workspace = "/tmp"

    class FakeInner:
        messages: list = []  # noqa: RUF012

    class FakeAgent:
        def __init__(self):
            self.config = FakeConfig()
            self._inner = FakeInner()

    agent = FakeAgent()
    manager = SessionManager(agent=agent)

    # Should not raise even if Strands parts fail
    path = manager.save_session("fail-test", agent)
    assert path.exists()
