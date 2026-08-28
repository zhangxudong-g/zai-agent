"""Optimized code-analysis agent: behavior tests (TDD RED→GREEN).

These tests cover the 4-layer overhaul:

  1. system_prompt — 4-stage protocol + 4-section output template
  2. read  — offset / limit / binary detection / max_bytes
  3. grep  — context lines / output_mode
  4. new tools — file_tree, outline (AST)
  5. project index — cache hits + mtime invalidation
  6. self-check — weak-assertion detection in final_text

No Ollama required — every test calls an inner factory or hook method
directly so they are deterministic.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

# Make the package importable when pytest is invoked from project root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# ============================================================ #
# Fixtures
# ============================================================ #
@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Small Python project in a tmp dir — covers defs, classes, binary."""
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "main.py").write_text(
        "class Greeter:\n"
        "    def __init__(self, name: str):\n"
        "        self.name = name\n"
        "    def greet(self) -> str:\n"
        "        return f'Hello, {self.name}'\n"
        "def main() -> None:\n"
        "    g = Greeter('world')\n"
        "    print(g.greet())\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "util.py").write_text(
        "def add(a, b):\n"
        "    return a + b\n"
        "def multiply(a, b):\n"
        "    return a * b\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_main.py").write_text("# placeholder\n", encoding="utf-8")
    # A binary blob (PNG-like bytes; not a real image, just non-UTF8).
    (tmp_path / "src" / "logo.bin").write_bytes(b"\x00\x01\x02\xff\xfe\xfdbinary")
    return tmp_path


def _make_agent_prompt():
    """Build an Agent instance WITHOUT calling __init__ (no Ollama needed)."""
    from strands_poc.agent import Agent

    agent = object.__new__(Agent)
    # Skipping __init__ means we must populate attrs the rebuilt _build_system_prompt may read.
    agent._use_community_tools = False
    return agent


# ============================================================ #
# Group 1: system prompt — protocol + output template
# ============================================================ #
def test_system_prompt_has_four_stages(workspace: Path):
    """system prompt must enumerate 4 ordered analysis stages."""
    agent = _make_agent_prompt()
    prompt = agent._build_system_prompt(mode="analysis")
    for stage in ("阶段1", "阶段2", "阶段3", "阶段4"):
        assert stage in prompt, f"missing stage marker {stage!r} in prompt"


def test_system_prompt_has_output_template_sections(workspace: Path):
    """system prompt must include all 4 required output sections."""
    agent = _make_agent_prompt()
    prompt = agent._build_system_prompt(mode="analysis")
    for section in ("## 范围", "## 证据", "## 结论", "## 不确定性"):
        assert section in prompt, f"missing section {section!r}"


def test_system_prompt_lists_available_tools(workspace: Path):
    """system prompt must advertise the actual tool names so the model picks them."""
    agent = _make_agent_prompt()
    prompt = agent._build_system_prompt()
    for tool_name in ("read", "grep", "file_tree", "outline"):
        assert tool_name in prompt, f"missing tool {tool_name!r} in prompt"


# ============================================================ #
# Group 2: read tool — offset/limit/binary/max_bytes
# ============================================================ #
def test_read_tool_returns_offset_slice(workspace: Path):
    """read(file_path, offset=2, limit=2) returns lines 3-4."""
    from strands_poc.tools import make_read_tool

    read = make_read_tool(workspace)
    out = read(file_path="src/main.py", offset=2, limit=2)
    # Source line 3 = "    def __init__..." ; line 4 = "        self.name = name"
    assert "def __init__" in out
    assert "self.name" in out
    assert out.count("\n") <= 2  # offset=2 limit=2 → at most 2 lines


def test_read_tool_detects_binary(workspace: Path):
    """read on a binary file returns a sentinel, not the raw bytes."""
    from strands_poc.tools import make_read_tool

    read = make_read_tool(workspace)
    out = read(file_path="src/logo.bin")
    assert "binary" in out.lower()
    # Must NOT contain the raw non-UTF8 bytes
    assert "\x00" not in out


def test_read_tool_truncates_large_files(workspace: Path):
    """read with max_bytes=N truncates the output and notes the truncation."""
    from strands_poc.tools import make_read_tool

    read = make_read_tool(workspace)
    big = workspace / "src" / "big.txt"
    big.write_text("X" * 5000, encoding="utf-8")
    out = read(file_path="src/big.txt", max_bytes=128)
    assert len(out) < 200  # should be truncated
    assert "truncat" in out.lower() or "超过" in out or "省略" in out


# ============================================================ #
# Group 3: grep tool — context / output_mode
# ============================================================ #
def test_grep_tool_includes_context_lines(workspace: Path):
    """grep(pattern, context=1) returns match + 1 line before/after."""
    from strands_poc.tools import make_grep_tool

    # main.py: line 4 = "        self.name = name" — match `self.name`
    grep = make_grep_tool(workspace)
    out = grep(pattern=r"self\.name = name", path="src", context=1)
    # Should include the match line AND its neighbors (def __init__ + self.name + return f'...)
    assert "def __init__" in out
    assert "self.name = name" in out
    assert "greet" in out  # line after


def test_grep_tool_files_with_matches_mode(workspace: Path):
    """grep(output_mode='files_with_matches') returns only file paths."""
    from strands_poc.tools import make_grep_tool

    grep = make_grep_tool(workspace)
    out = grep(pattern=r"def", path="src", output_mode="files_with_matches")
    # No line content — just file paths, no "lineno:" prefix.
    assert ":1:" not in out and ":2:" not in out
    assert "src/main.py" in out or "src/util.py" in out


# ============================================================ #
# Group 4: new tools — file_tree, outline
# ============================================================ #
def test_file_tree_lists_files_and_dirs(workspace: Path):
    """file_tree returns structured tree with size info."""
    from strands_poc.tools import make_file_tree_tool

    ft = make_file_tree_tool(workspace)
    out = ft(max_depth=3)
    data = json.loads(out)
    # data is a list of {"path": ..., "type": "file"|"dir", "size": int|None}
    paths = {e["path"] for e in data}
    assert "src" in paths
    assert "src/main.py" in paths
    assert any(e["type"] == "dir" for e in data)
    assert any(e["type"] == "file" and e.get("size", 0) > 0 for e in data)


def test_outline_extracts_python_defs(workspace: Path):
    """outline extracts class/function names from a Python file."""
    from strands_poc.tools import make_outline_tool

    outline = make_outline_tool(workspace)
    out = outline(file_path="src/main.py")
    assert "Greeter" in out
    assert "__init__" in out
    assert "greet" in out
    assert "main" in out  # the top-level main()


# ============================================================ #
# Group 5: project index — cache + invalidation
# ============================================================ #
def test_project_index_caches_within_ttl(workspace: Path):
    """Within TTL, second call to file_tree returns cached result."""
    from strands_poc.index import ProjectIndex

    idx = ProjectIndex(workspace, ttl_seconds=60)
    first = idx.get_tree(max_depth=3)
    second = idx.get_tree(max_depth=3)
    assert first == second
    # Cached flag must be set on second call
    assert idx.last_hit_cache is True


def test_project_index_invalidates_on_mtime_change(workspace: Path):
    """After mutating a file, next call invalidates the cache."""
    from strands_poc.index import ProjectIndex

    idx = ProjectIndex(workspace, ttl_seconds=60)
    idx.get_tree(max_depth=3)
    # Mutate an existing file
    target = workspace / "src" / "main.py"
    new_content = target.read_text(encoding="utf-8") + "\n# touched\n"
    # Force mtime to advance even on filesystems with 1-second resolution
    time.sleep(1.1)
    target.write_text(new_content, encoding="utf-8")
    idx.get_tree(max_depth=3)
    assert idx.last_hit_cache is False


# ============================================================ #
# Group 6: self-check — weak-assertion detection
# ============================================================ #
def test_self_check_flags_weak_assertion_words(workspace: Path):
    """detect_weak_assertions finds 我猜/大概/可能/也许 etc."""
    from strands_poc.agent import Agent

    text = "我们大概可能也许不全是这样，但这是我的判断。"
    findings = Agent.detect_weak_assertions(text)
    assert "大概" in findings
    assert "可能" in findings
    assert "也许" in findings
    assert "判断" not in findings  # only weak tokens, not every soft word


def test_self_check_returns_empty_for_strong_text(workspace: Path):
    """detect_weak_assertions returns empty list for evidence-backed prose."""
    from strands_poc.agent import Agent

    text = "在 src/main.py:5，self.name 字段由 __init__ 写入。"
    findings = Agent.detect_weak_assertions(text)
    assert findings == []
