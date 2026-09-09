"""End-to-end tests for 0.3.0 features."""

import pytest
import json
from pathlib import Path
from zai.snapshot import Snapshot, save_snapshot, load_snapshot
from zai.session_manager import SessionManager
from zai.context_loader import ContextLoader, load_project_context, ProjectContext


def test_snapshot_save_load_roundtrip(tmp_path):
    """Test snapshot save and load."""
    snapshot = Snapshot(name="test")
    snapshot.add_message("user", "Hello")
    snapshot.add_message("assistant", "Hi")

    path = tmp_path / "test.json"
    save_snapshot(snapshot, path)

    loaded = load_snapshot(path)
    assert loaded.name == "test"
    assert len(loaded.messages) == 2


def test_context_loader_all_files(tmp_path):
    """Test loading all context files."""
    zai_dir = tmp_path / ".zai"
    zai_dir.mkdir()

    (zai_dir / "context.md").write_text("# Context")
    (zai_dir / "rules.md").write_text("# Rules")
    (zai_dir / "ignore").write_text("*.pyc")

    ctx = load_project_context(tmp_path)
    assert ctx.context == "# Context"
    assert ctx.rules == "# Rules"
    assert "*.pyc" in ctx.ignore_patterns


def test_session_manager_integration(tmp_path):
    """Test session manager with snapshot."""
    # Use session manager's save_session to save
    sm = SessionManager(saves_dir=tmp_path)
    
    # Create a mock config and session log
    class MockConfig:
        ollama_base_url = "http://localhost:11434"
        ollama_model = "qwen3:1.7b"
        agent_workspace = tmp_path
    
    # Create a mock session log
    session_log = tmp_path / "session.jsonl"
    session_log.write_text('{"event": "user_message", "content": "Test"}\n')
    
    path = sm.save_session("integration-test", MockConfig(), session_log)
    assert path.exists()

    # Use session manager
    sessions = sm.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["name"] == "integration-test"

    # Load session
    loaded = sm.load_session("integration-test")
    assert loaded.name == "integration-test"
    assert len(loaded.messages) == 1  # From JSONL (Test message)
    assert loaded.config["ollama_model"] == "qwen3:1.7b"
    assert loaded.config["workspace"] == str(tmp_path)


def test_project_context_to_system_prompt():
    """Test project context converts to system prompt correctly."""
    ctx = ProjectContext(
        context="This is a test project",
        rules="Follow PEP 8 style",
        ignore_patterns=["node_modules"]
    )

    prompt = ctx.to_system_prompt()

    assert "## 项目上下文" in prompt
    assert "This is a test project" in prompt
    assert "## 项目规则" in prompt
    assert "Follow PEP 8 style" in prompt


def test_snapshot_with_tool_calls(tmp_path):
    """Test snapshot preserves tool calls."""
    snapshot = Snapshot(name="tool-test")
    snapshot.add_message("user", "Read the file")
    snapshot.add_message("assistant", "I'll read the file")
    snapshot.add_tool_call("read", {"path": "test.py"}, "file content")

    path = tmp_path / "tool-test.json"
    save_snapshot(snapshot, path)

    loaded = load_snapshot(path)
    assert len(loaded.tool_calls) == 1
    assert loaded.tool_calls[0]["tool"] == "read"
    assert loaded.tool_calls[0]["args"]["path"] == "test.py"
    assert loaded.tool_calls[0]["result"] == "file content"


def test_export_session(tmp_path):
    """Test session export functionality."""
    # Create mock JSONL
    jsonl_file = tmp_path / "export_test.jsonl"
    jsonl_content = json.dumps({"event": "user_message", "content": "Export me"}) + "\n"
    jsonl_file.write_text(jsonl_content)

    exports_dir = tmp_path / "exports"
    sm = SessionManager(saves_dir=tmp_path / "saves", exports_dir=exports_dir)

    path = sm.export_session("exported-session", jsonl_file)

    assert path.exists()
    assert "exported-session" in path.name

    # Verify exported content
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["name"] == "exported-session"
    assert len(data["messages"]) == 1
    assert data["messages"][0]["content"] == "Export me"
