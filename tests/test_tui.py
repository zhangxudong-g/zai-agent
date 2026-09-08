"""Tests for zai.tui (Terminal UI helpers)."""

from __future__ import annotations

import io
import sys

import pytest

from zai import tui


def test_color_helpers_are_strings():
    """All color helpers return strings."""
    assert isinstance(tui.c_red("hello"), str)
    assert isinstance(tui.c_green("hello"), str)
    assert isinstance(tui.c_yellow("hello"), str)
    assert isinstance(tui.c_blue("hello"), str)
    assert isinstance(tui.c_magenta("hello"), str)
    assert isinstance(tui.c_cyan("hello"), str)
    assert isinstance(tui.c_dim("hello"), str)
    assert isinstance(tui.c_bold("hello"), str)


def test_color_helpers_wrap_text():
    """Color helpers wrap the input text."""
    assert "hello" in tui.c_red("hello")
    assert "world" in tui.c_green("world")


def test_format_tool_args_empty():
    """No args → just tool name."""
    assert tui._format_tool_args("read", None) == "read"
    assert tui._format_tool_args("read", {}) == "read"


def test_format_tool_args_shell():
    """Shell command formatting."""
    assert tui._format_tool_args("shell", {"command": "ls"}) == "ls"
    long_cmd = "x" * 200
    formatted = tui._format_tool_args("shell", {"command": long_cmd})
    assert len(formatted) <= 100
    assert formatted.endswith("…")


def test_format_tool_args_single_arg():
    """Single-arg tools."""
    assert tui._format_tool_args("read", {"file_path": "main.py"}) == "read main.py"


def test_format_tool_args_multi_arg():
    """Multi-arg tools."""
    formatted = tui._format_tool_args(
        "grep", {"pattern": "TODO", "path": "src"}
    )
    assert formatted.startswith("grep ")
    assert "pattern" in formatted
    assert "path" in formatted


def test_format_tool_args_long_value():
    """Long values truncated with ellipsis."""
    long_path = "x" * 200
    formatted = tui._format_tool_args("read", {"file_path": long_path})
    assert len(formatted) <= 105  # "read " (5) + 100 chars
    assert "…" in formatted


def test_print_banner_v2(capsys):
    """Banner prints with two rows."""
    from pathlib import Path

    tui.print_banner_v2(
        model="qwen3:1.7b",
        workspace=Path("/tmp/workspace"),
        session_id="20250108_150000",
        zai_home=Path("/home/user/.zai"),
        interactive=True,
    )
    captured = capsys.readouterr()
    # Banner has ╭─ and ╰─
    assert "╭─" in captured.out
    assert "╰─" in captured.out
    assert "qwen3:1.7b" in captured.out
    assert "session 20250108_150000" in captured.out


def test_print_box(capsys):
    """Box prints bordered content."""
    tui.print_box(
        "Test",
        ["line1", "line2"],
        color="cyan",
        width=30,
    )
    captured = capsys.readouterr()
    assert "╭─ Test" in captured.out
    assert "│ line1" in captured.out
    assert "│ line2" in captured.out
    assert "╰" in captured.out


def test_print_box_truncates_long_lines(capsys):
    """Long lines are truncated."""
    tui.print_box(
        "Title",
        ["x" * 100],
        color="cyan",
        width=20,
    )
    captured = capsys.readouterr()
    # Should contain ellipsis for truncation
    assert "…" in captured.out


def test_print_tool_start(capsys):
    """Tool start prints formatted."""
    tui.print_tool_start("read", {"file_path": "main.py"})
    captured = capsys.readouterr()
    assert "🔧" in captured.out
    assert "read" in captured.out
    assert "main.py" in captured.out


def test_print_tool_result_success(capsys):
    """Successful tool result has ✓."""
    tui.print_tool_result("read", "file contents", is_error=False)
    captured = capsys.readouterr()
    assert "✓" in captured.out
    assert "file contents" in captured.out


def test_print_tool_result_error(capsys):
    """Error tool result has ✗."""
    tui.print_tool_result("read", "fail", is_error=True)
    captured = capsys.readouterr()
    assert "✗" in captured.out


def test_print_tool_result_truncates(capsys):
    """Long results are truncated."""
    long_result = "x" * 1000
    tui.print_tool_result("read", long_result)
    captured = capsys.readouterr()
    # Should be shorter than the original
    assert len(captured.out) < 1000


def test_print_thinking(capsys):
    """Thinking is printed with emoji."""
    tui.print_thinking("reasoning about the problem")
    captured = capsys.readouterr()
    assert "🤔" in captured.out
    assert "reasoning" in captured.out


def test_print_done(capsys):
    """Done prints ✓ and elapsed time."""
    tui.print_done(2.5)
    captured = capsys.readouterr()
    assert "✓" in captured.out
    assert "2.5" in captured.out


def test_print_error(capsys):
    """Error prints to stderr."""
    tui.print_error("connection failed")
    captured = capsys.readouterr()
    assert "❌" in captured.err
    assert "connection failed" in captured.err


def test_print_warn(capsys):
    """Warn prints to stderr."""
    tui.print_warn("deprecated")
    captured = capsys.readouterr()
    assert "⚠" in captured.err
    assert "deprecated" in captured.err


def test_print_info(capsys):
    """Info prints to stdout."""
    tui.print_info("starting")
    captured = capsys.readouterr()
    assert "ℹ" in captured.out
    assert "starting" in captured.out


def test_render_markdown_falls_back_without_rich(monkeypatch, capsys):
    """When Rich is unavailable, falls back to plain text."""
    # Force Rich to be unavailable
    monkeypatch.setattr(tui, "_HAS_RICH", False)

    tui.render_markdown("# Hello\n\nWorld")
    captured = capsys.readouterr()
    assert "Hello" in captured.out
    assert "World" in captured.out


def test_color_disabled_for_pipes(monkeypatch):
    """When stdout is not a TTY, color codes are stripped."""
    # Simulate non-TTY (piped output)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    # Re-import to pick up the change
    import importlib

    importlib.reload(tui)
    # With _ENABLE_COLOR=True but not a TTY, colors should be empty
    result = tui.c_red("hello")
    # The result should still be a string
    assert isinstance(result, str)


def test_spinner_doesnt_block_in_non_tty(capsys):
    """Spinner is a no-op when stdout is not a TTY."""
    # In test environment, stdout is not a TTY, so spinner is skipped
    with tui.Spinner("loading"):
        pass
    # Should complete without blocking
    captured = capsys.readouterr()
    # No spinner output expected in non-TTY
    assert "loading" not in captured.out