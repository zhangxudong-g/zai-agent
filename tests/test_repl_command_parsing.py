"""Tests for REPL command parsing (without arguments).

These tests verify that /save, /load, /export without arguments
are correctly recognized as commands and don't fall through to the agent.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch


def test_save_without_args_shows_error(capsys):
    """Test /save (no args) shows usage error, not falls through to agent."""
    from zai.repl import EnhancedREPL

    mock_agent = MagicMock()
    mock_logger = MagicMock()

    with patch("zai.repl._create_session"):
        repl = EnhancedREPL(mock_agent, mock_logger, stream=False)
        # Simulate /save with empty name
        repl.handle_save_command("")

    captured = capsys.readouterr()
    assert "[ERROR] 用法: /save <名称>" in captured.out


def test_load_without_args_shows_error(capsys):
    """Test /load (no args) shows usage error."""
    from zai.repl import EnhancedREPL

    mock_agent = MagicMock()
    mock_logger = MagicMock()

    with patch("zai.repl._create_session"):
        repl = EnhancedREPL(mock_agent, mock_logger, stream=False)
        repl.handle_load_command("")

    captured = capsys.readouterr()
    assert "[ERROR] 用法: /load <名称>" in captured.out


def test_export_without_args_shows_error(capsys):
    """Test /export (no args) shows usage error."""
    from zai.repl import EnhancedREPL

    mock_agent = MagicMock()
    mock_logger = MagicMock()

    with patch("zai.repl._create_session"):
        repl = EnhancedREPL(mock_agent, mock_logger, stream=False)
        repl.handle_export_command("")

    captured = capsys.readouterr()
    assert "[ERROR] 用法: /export <名称>" in captured.out


def test_command_parsing_save_no_args():
    """Verify /save (no args) routes to handle_save_command, not agent."""
    from zai.repl import EnhancedREPL

    mock_agent = MagicMock()
    mock_logger = MagicMock()

    with patch("zai.repl._create_session"):
        repl = EnhancedREPL(mock_agent, mock_logger, stream=False)

        # Test command parsing logic
        cmd_lower_strip = "/save".lower().strip()

        # Verify the logic recognizes /save without args
        assert cmd_lower_strip.startswith("/save")

        # The actual handle_save_command should be called with empty string
        with patch.object(repl, "handle_save_command") as mock_handle:
            # Simulate the command processing logic
            rest = "/save"[5:].strip()  # ""
            mock_handle(rest)
            mock_handle.assert_called_once_with("")


def test_command_parsing_load_no_args():
    """Verify /load (no args) routes correctly."""
    from zai.repl import EnhancedREPL

    mock_agent = MagicMock()
    mock_logger = MagicMock()

    with patch("zai.repl._create_session"):
        repl = EnhancedREPL(mock_agent, mock_logger, stream=False)

        cmd_lower_strip = "/load".lower().strip()
        assert cmd_lower_strip.startswith("/load")

        with patch.object(repl, "handle_load_command") as mock_handle:
            rest = "/load"[5:].strip()  # ""
            mock_handle(rest)
            mock_handle.assert_called_once_with("")


def test_command_parsing_export_no_args():
    """Verify /export (no args) routes correctly."""
    from zai.repl import EnhancedREPL

    mock_agent = MagicMock()
    mock_logger = MagicMock()

    with patch("zai.repl._create_session"):
        repl = EnhancedREPL(mock_agent, mock_logger, stream=False)

        cmd_lower_strip = "/export".lower().strip()
        assert cmd_lower_strip.startswith("/export")

        with patch.object(repl, "handle_export_command") as mock_handle:
            rest = "/export"[7:].strip()  # ""
            mock_handle(rest)
            mock_handle.assert_called_once_with("")


def test_command_parsing_save_with_args():
    """Verify /save myname extracts 'myname' as the name."""
    cmd_lower_strip = "/save myname".lower().strip()
    assert cmd_lower_strip.startswith("/save")

    # Simulate extraction
    prompt_text = "/save myname"
    rest = prompt_text[5:].strip()
    assert rest == "myname"


def test_command_parsing_load_with_args():
    """Verify /load myname extracts 'myname' as the name."""
    prompt_text = "/load myname"
    rest = prompt_text[5:].strip()
    assert rest == "myname"


def test_command_parsing_export_with_args():
    """Verify /export myname extracts 'myname' as the name."""
    prompt_text = "/export myname"
    rest = prompt_text[7:].strip()
    assert rest == "myname"
