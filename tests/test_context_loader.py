"""Tests for context loader module."""

from zai.context_loader import ContextLoader, ProjectContext, load_project_context


def test_load_context_no_files(tmp_path):
    """Test loading when .zai directory doesn't exist."""
    loader = ContextLoader(tmp_path)
    ctx = loader.load()

    assert ctx.context == ""
    assert ctx.rules == ""
    assert ctx.ignore_patterns == []


def test_load_context_with_files(tmp_path):
    """Test loading all context files."""
    zai_dir = tmp_path / ".zai"
    zai_dir.mkdir()

    (zai_dir / "context.md").write_text("# Context\nTest project")
    (zai_dir / "rules.md").write_text("# Rules\nPEP 8")
    (zai_dir / "ignore").write_text("node_modules\n*.pyc")

    loader = ContextLoader(tmp_path)
    ctx = loader.load()

    assert "Test project" in ctx.context
    assert "PEP 8" in ctx.rules
    assert "node_modules" in ctx.ignore_patterns
    assert "*.pyc" in ctx.ignore_patterns


def test_parse_ignore_comments(tmp_path):
    """Test that comments are skipped in ignore file."""
    zai_dir = tmp_path / ".zai"
    zai_dir.mkdir()

    (zai_dir / "ignore").write_text("# This is a comment\nnode_modules\n# Another comment\n*.pyc\n")

    loader = ContextLoader(tmp_path)
    ctx = loader.load()

    assert len(ctx.ignore_patterns) == 2
    assert "node_modules" in ctx.ignore_patterns
    assert "*.pyc" in ctx.ignore_patterns


def test_project_context_is_empty():
    """Test is_empty method."""
    empty = ProjectContext()
    assert empty.is_empty() is True

    non_empty = ProjectContext(context="Some context")
    assert non_empty.is_empty() is False


def test_to_system_prompt():
    """Test converting context to system prompt."""
    ctx = ProjectContext(
        context="Project description", rules="Follow PEP 8", ignore_patterns=["node_modules"]
    )

    prompt = ctx.to_system_prompt()

    assert "## 项目上下文" in prompt
    assert "Project description" in prompt
    assert "## 项目规则" in prompt
    assert "Follow PEP 8" in prompt


def test_to_system_prompt_empty():
    """Test to_system_prompt with empty context."""
    ctx = ProjectContext()
    prompt = ctx.to_system_prompt()
    assert prompt == ""


def test_load_project_context_convenience(tmp_path):
    """Test the convenience function."""
    zai_dir = tmp_path / ".zai"
    zai_dir.mkdir()

    (zai_dir / "context.md").write_text("Test context")

    ctx = load_project_context(tmp_path)
    assert ctx.context == "Test context"


def test_ignore_empty_lines(tmp_path):
    """Test that empty lines are skipped in ignore file."""
    zai_dir = tmp_path / ".zai"
    zai_dir.mkdir()

    (zai_dir / "ignore").write_text("node_modules\n\n*.pyc\n\n")

    loader = ContextLoader(tmp_path)
    ctx = loader.load()

    assert len(ctx.ignore_patterns) == 2
    assert "" not in ctx.ignore_patterns
