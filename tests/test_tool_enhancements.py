"""Tests for tool enhancements: edit, read, shell, encoding detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from zai.tools import (
    _atomic_write,
    _detect_encoding,
    _looks_binary,
    _make_diff,
    make_diff_tool,
    make_edit_tool,
    make_read_tool,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a workspace directory."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


# --------------------------------------------------------------------- #
# Encoding detection
# --------------------------------------------------------------------- #


def test_detect_encoding_utf8_bom():
    """UTF-8 BOM is detected."""
    raw = b"\xef\xbb\xbfhello"
    enc, conf = _detect_encoding(raw)
    assert enc == "utf-8-sig"
    assert conf >= 0.9


def test_detect_encoding_utf16_le_bom():
    """UTF-16 LE BOM is detected."""
    raw = b"\xff\xfeh\x00i\x00"
    enc, _ = _detect_encoding(raw)
    assert enc in ("utf-16", "utf-16-le")


def test_detect_encoding_gbk():
    """GBK encoded text is detected."""
    raw = "你好世界".encode("gbk")
    enc, _ = _detect_encoding(raw)
    # charset_normalizer may report cp949/gbk/gb2312 depending on heuristics
    # any non-utf-8 detection is acceptable for non-ASCII Chinese
    assert enc != "utf-8"
    # And should still decode correctly with the detected encoding
    raw.decode(enc)


def test_detect_encoding_utf8_pure():
    """Pure ASCII (valid UTF-8) is detected as utf-8 compatible."""
    raw = b"hello world"
    enc, _ = _detect_encoding(raw)
    # charset_normalizer may report 'ascii' for pure ASCII, which is a
    # subset of utf-8 and decodes correctly.
    assert enc in ("utf-8", "ascii")
    raw.decode(enc)


# --------------------------------------------------------------------- #
# Atomic write
# --------------------------------------------------------------------- #


def test_atomic_write_creates_file(workspace):
    """Atomic write creates the target file."""
    target = workspace / "test.txt"
    _atomic_write(target, "hello")
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "hello"


def test_atomic_write_overwrites(workspace):
    """Atomic write overwrites existing file."""
    target = workspace / "test.txt"
    target.write_text("old", encoding="utf-8")
    _atomic_write(target, "new")
    assert target.read_text(encoding="utf-8") == "new"


def test_atomic_write_cleans_up_tmp(workspace):
    """Tmp file is cleaned up on success."""
    target = workspace / "test.txt"
    _atomic_write(target, "hello")
    tmp = target.with_suffix(target.suffix + ".tmp")
    assert not tmp.exists()


# --------------------------------------------------------------------- #
# Diff helper
# --------------------------------------------------------------------- #


def test_make_diff_shows_changes():
    """Diff shows added/removed lines."""
    old = "line1\nline2\nline3\n"
    new = "line1\nline2-modified\nline3\n"
    diff = _make_diff(old, new, "test.txt")
    assert "test.txt" in diff
    assert "-line2" in diff or "-line2\n" in diff
    assert "+line2-modified" in diff


def test_make_diff_empty_when_unchanged():
    """Diff is empty when content is unchanged."""
    text = "line1\nline2\n"
    diff = _make_diff(text, text, "test.txt")
    assert diff == ""


# --------------------------------------------------------------------- #
# Read tool
# --------------------------------------------------------------------- #


def test_read_basic_utf8(workspace):
    """Read a UTF-8 file."""
    target = workspace / "test.txt"
    target.write_text("hello\nworld\n", encoding="utf-8")
    tool = make_read_tool(workspace)
    result = tool(target.name)
    assert "hello" in result
    assert "world" in result


def test_read_with_offset_limit(workspace):
    """Read with offset and limit."""
    target = workspace / "test.txt"
    target.write_text("\n".join(f"line{i}" for i in range(1, 11)), encoding="utf-8")
    tool = make_read_tool(workspace)
    result = tool(target.name, offset=3, limit=2)
    assert "line3" in result
    assert "line4" in result
    assert "line5" not in result


def test_read_binary_returns_sentinel(workspace):
    """Binary files return a sentinel, not garbled text."""
    target = workspace / "binary.bin"
    target.write_bytes(b"\x00\x01\x02\x03\x04binary stuff\x00")
    tool = make_read_tool(workspace)
    result = tool(target.name)
    assert "[binary" in result
    assert "bytes" in result


def test_read_large_file_protection(workspace):
    """Large files are preview-protected."""
    target = workspace / "big.txt"
    target.write_bytes(b"x" * 2_000_000)  # 2MB
    tool = make_read_tool(workspace)
    result = tool(target.name)
    assert "truncated" in result.lower() or "preview" in result.lower()


def test_read_gbk_file(workspace):
    """GBK files are auto-detected."""
    target = workspace / "chinese.txt"
    text = "你好世界" * 100
    target.write_bytes(text.encode("gbk"))
    tool = make_read_tool(workspace)
    result = tool(target.name)
    # Should mention encoding or show readable text
    assert "encoding:" in result.lower() or "你好" in result


# --------------------------------------------------------------------- #
# Edit tool
# --------------------------------------------------------------------- #


def test_edit_unique_match(workspace):
    """Edit with a unique match succeeds."""
    target = workspace / "test.py"
    target.write_text("foo\nbar\nbaz\n", encoding="utf-8")
    tool = make_edit_tool(workspace)
    result = tool(target.name, "bar", "BAR")
    assert "Edited" in result
    assert target.read_text(encoding="utf-8") == "foo\nBAR\nbaz\n"


def test_edit_multiple_matches_fails_by_default(workspace):
    """Edit fails when old_string matches multiple times without replace_all."""
    target = workspace / "test.py"
    target.write_text("foo\nfoo\nfoo\n", encoding="utf-8")
    tool = make_edit_tool(workspace)
    result = tool(target.name, "foo", "bar")
    assert "ERROR" in result
    assert "3 times" in result


def test_edit_replace_all(workspace):
    """Edit with replace_all=True replaces every match."""
    target = workspace / "test.py"
    target.write_text("foo\nfoo\nfoo\n", encoding="utf-8")
    tool = make_edit_tool(workspace)
    result = tool(target.name, "foo", "bar", replace_all=True)
    assert "3 replacements" in result
    assert target.read_text(encoding="utf-8") == "bar\nbar\nbar\n"


def test_edit_no_match_returns_error(workspace):
    """Edit returns error when old_string is not found."""
    target = workspace / "test.py"
    target.write_text("foo\nbar\n", encoding="utf-8")
    tool = make_edit_tool(workspace)
    result = tool(target.name, "nonexistent", "x")
    assert "ERROR" in result
    assert "No match" in result


def test_edit_dry_run_does_not_write(workspace):
    """Edit with dry_run=True returns diff without writing."""
    target = workspace / "test.py"
    target.write_text("foo\nbar\n", encoding="utf-8")
    tool = make_edit_tool(workspace)
    result = tool(target.name, "bar", "BAR", dry_run=True)
    assert "DRY RUN" in result
    # File should not be modified
    assert target.read_text(encoding="utf-8") == "foo\nbar\n"


def test_edit_atomic_write(workspace):
    """Edit uses atomic writes (no leftover tmp file)."""
    target = workspace / "test.py"
    target.write_text("foo\nbar\n", encoding="utf-8")
    tool = make_edit_tool(workspace)
    tool(target.name, "bar", "BAR")
    tmp = target.with_suffix(target.suffix + ".tmp")
    assert not tmp.exists()


# --------------------------------------------------------------------- #
# Diff tool
# --------------------------------------------------------------------- #


def test_diff_previews_change(workspace):
    """Diff tool previews changes without writing."""
    target = workspace / "test.py"
    target.write_text("foo\nbar\nbaz\n", encoding="utf-8")
    tool = make_diff_tool(workspace)
    result = tool(target.name, "bar", "BAR")
    assert "test.py" in result
    # File unchanged
    assert target.read_text(encoding="utf-8") == "foo\nbar\nbaz\n"


def test_diff_no_match_returns_error(workspace):
    """Diff tool returns error when old_string not found."""
    target = workspace / "test.py"
    target.write_text("foo\nbar\n", encoding="utf-8")
    tool = make_diff_tool(workspace)
    result = tool(target.name, "nonexistent", "x")
    assert "ERROR" in result


def test_diff_multiple_matches_without_replace_all(workspace):
    """Diff tool fails on ambiguous match by default."""
    target = workspace / "test.py"
    target.write_text("foo\nfoo\n", encoding="utf-8")
    tool = make_diff_tool(workspace)
    result = tool(target.name, "foo", "bar")
    assert "ERROR" in result
    assert "2 times" in result


# --------------------------------------------------------------------- #
# Binary detection
# --------------------------------------------------------------------- #


def test_looks_binary_detects_null_bytes():
    """Files with null bytes are detected as binary."""
    assert _looks_binary(b"\x00\x01\x02")
    assert _looks_binary(b"text\x00binary")


def test_looks_binary_passes_text():
    """Pure text files are not detected as binary."""
    assert not _looks_binary(b"hello world")
    assert not _looks_binary(b"def foo():\n    return 42\n")
