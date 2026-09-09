"""Tests for REPL session commands.

Note: These tests mock REPL internals to avoid prompt_toolkit console requirements.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch


def test_handle_sessions_command_empty(capsys):
    """Test /sessions command with no sessions."""
    from zai.repl import EnhancedREPL

    mock_agent = MagicMock()
    mock_logger = MagicMock()

    # Mock the session creation to avoid prompt_toolkit
    with patch("zai.repl._create_session"):
        repl = EnhancedREPL(mock_agent, mock_logger, stream=False)

        with patch("zai.repl.get_session_manager") as mock_sm:
            mock_sm.return_value.list_sessions.return_value = []
            repl.handle_sessions_command()

    captured = capsys.readouterr()
    assert "暂无保存的会话" in captured.out


def test_handle_sessions_command_with_sessions(capsys):
    """Test /sessions command with saved sessions."""
    from zai.repl import EnhancedREPL

    mock_agent = MagicMock()
    mock_logger = MagicMock()

    with patch("zai.repl._create_session"):
        repl = EnhancedREPL(mock_agent, mock_logger, stream=False)

        with patch("zai.repl.get_session_manager") as mock_sm:
            mock_sm.return_value.list_sessions.return_value = [
                {"name": "project1", "created_at": "2025-01-01T10:00:00Z", "message_count": 5},
                {"name": "project2", "created_at": "2025-01-02T10:00:00Z", "message_count": 10},
            ]
            repl.handle_sessions_command()

    captured = capsys.readouterr()
    assert "project1" in captured.out
    assert "project2" in captured.out


def test_handle_save_command_no_name(capsys):
    """Test /save without name shows error."""
    from zai.repl import EnhancedREPL

    mock_agent = MagicMock()
    mock_logger = MagicMock()

    with patch("zai.repl._create_session"):
        repl = EnhancedREPL(mock_agent, mock_logger, stream=False)
        repl.handle_save_command("")

    captured = capsys.readouterr()
    assert "[ERROR] 用法: /save <名称>" in captured.out


def test_handle_save_command_success(capsys):
    """Test /save with valid name."""
    from zai.repl import EnhancedREPL

    mock_agent = MagicMock()
    mock_logger = MagicMock()
    mock_logger.log_file = Path("test.jsonl")

    with patch("zai.repl._create_session"):
        repl = EnhancedREPL(mock_agent, mock_logger, stream=False)

        with patch("zai.repl.get_session_manager") as mock_sm:
            mock_sm.return_value.save_session.return_value = Path("saves/test.json")
            repl.handle_save_command("test")

    captured = capsys.readouterr()
    assert "会话已保存" in captured.out


def test_handle_load_command_no_name(capsys):
    """Test /load without name shows error."""
    from zai.repl import EnhancedREPL

    mock_agent = MagicMock()
    mock_logger = MagicMock()

    with patch("zai.repl._create_session"):
        repl = EnhancedREPL(mock_agent, mock_logger, stream=False)
        repl.handle_load_command("")

    captured = capsys.readouterr()
    assert "[ERROR] 用法: /load <名称>" in captured.out


def test_handle_load_command_not_found(capsys):
    """Test /load with non-existent session."""
    from zai.repl import EnhancedREPL

    mock_agent = MagicMock()
    mock_logger = MagicMock()

    with patch("zai.repl._create_session"):
        repl = EnhancedREPL(mock_agent, mock_logger, stream=False)

        with patch("zai.repl.get_session_manager") as mock_sm:
            mock_sm.return_value.restore_session.side_effect = FileNotFoundError(
                "Session not found"
            )
            repl.handle_load_command("nonexistent")

    captured = capsys.readouterr()
    assert "会话不存在" in captured.out


def test_handle_export_command_no_name(capsys):
    """Test /export without name shows error."""
    from zai.repl import EnhancedREPL

    mock_agent = MagicMock()
    mock_logger = MagicMock()

    with patch("zai.repl._create_session"):
        repl = EnhancedREPL(mock_agent, mock_logger, stream=False)
        repl.handle_export_command("")

    captured = capsys.readouterr()
    assert "[ERROR] 用法: /export <名称>" in captured.out


def test_handle_export_command_success(capsys):
    """Test /export with valid name."""
    from zai.repl import EnhancedREPL

    mock_agent = MagicMock()
    mock_logger = MagicMock()
    mock_logger.log_file = Path("test.jsonl")

    with patch("zai.repl._create_session"):
        repl = EnhancedREPL(mock_agent, mock_logger, stream=False)

        with patch("zai.repl.get_session_manager") as mock_sm:
            mock_sm.return_value.export_session.return_value = Path("exports/test.json")
            repl.handle_export_command("test")

    captured = capsys.readouterr()
    assert "会话已导出" in captured.out
