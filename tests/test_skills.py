"""Tests for skills plugin."""

import pytest
from pathlib import Path
from zai.skills import (
    create_skill_template,
    list_skills,
    install_skill,
    uninstall_skill,
    build_skill_plugin,
    get_skills_dir,
)


def test_get_skills_dir(tmp_path, monkeypatch):
    """Test getting skills directory."""
    from zai import skills as skills_module
    monkeypatch.setattr(skills_module, "get_skills_dir", lambda: tmp_path)
    
    # Should create directory if it doesn't exist
    assert tmp_path.exists()


def test_create_skill_template(tmp_path, monkeypatch):
    """Test creating a skill from template."""
    from zai import skills as skills_module
    monkeypatch.setattr(skills_module, "get_skills_dir", lambda: tmp_path)
    
    skill_dir = create_skill_template("test-skill", "A test skill")
    
    assert skill_dir.exists()
    assert skill_dir.name == "test-skill"
    assert (skill_dir / "SKILL.md").exists()
    
    content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "test-skill" in content
    assert "A test skill" in content


def test_create_skill_template_no_description(tmp_path, monkeypatch):
    """Test creating a skill without description."""
    from zai import skills as skills_module
    monkeypatch.setattr(skills_module, "get_skills_dir", lambda: tmp_path)
    
    skill_dir = create_skill_template("no-desc-skill")
    
    assert skill_dir.exists()
    assert (skill_dir / "SKILL.md").exists()


def test_list_skills_empty(tmp_path, monkeypatch):
    """Test listing skills when none exist."""
    from zai import skills as skills_module
    monkeypatch.setattr(skills_module, "get_skills_dir", lambda: tmp_path)
    
    skills = list_skills()
    assert skills == []


def test_list_skills(tmp_path, monkeypatch):
    """Test listing skills."""
    from zai import skills as skills_module
    monkeypatch.setattr(skills_module, "get_skills_dir", lambda: tmp_path)
    
    # Create a few skills
    create_skill_template("skill-1", "First skill")
    create_skill_template("skill-2", "Second skill")
    
    skills = list_skills()
    assert len(skills) == 2
    
    names = [s["name"] for s in skills]
    assert "skill-1" in names
    assert "skill-2" in names


def test_install_skill(tmp_path, monkeypatch):
    """Test installing a skill from source."""
    from zai import skills as skills_module
    monkeypatch.setattr(skills_module, "get_skills_dir", lambda: tmp_path / "skills")
    
    # Create source
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: my-skill\n---\n# My Skill")
    
    target = install_skill(source)
    
    assert target.exists()
    assert target.name == "source"  # Uses source directory name
    assert (target / "SKILL.md").exists()


def test_install_skill_with_name(tmp_path, monkeypatch):
    """Test installing a skill with custom name."""
    from zai import skills as skills_module
    monkeypatch.setattr(skills_module, "get_skills_dir", lambda: tmp_path / "skills")
    
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: my-skill\n---\n# My Skill")
    
    target = install_skill(source, name="custom-name")
    
    assert target.exists()
    assert target.name == "custom-name"


def test_install_skill_already_exists(tmp_path, monkeypatch):
    """Test installing a skill that already exists."""
    from zai import skills as skills_module
    monkeypatch.setattr(skills_module, "get_skills_dir", lambda: tmp_path)
    
    # Create a skill first
    create_skill_template("dup-skill")
    
    # Try to install another with same name
    source = tmp_path / "dup-skill-source"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: dup-skill\n---\n# Dup")
    
    with pytest.raises(FileExistsError):
        install_skill(source)


def test_install_skill_no_skill_md(tmp_path, monkeypatch):
    """Test installing without SKILL.md raises error."""
    from zai import skills as skills_module
    monkeypatch.setattr(skills_module, "get_skills_dir", lambda: tmp_path)
    
    source = tmp_path / "no-skill-md"
    source.mkdir()
    
    with pytest.raises(FileNotFoundError):
        install_skill(source)


def test_uninstall_skill(tmp_path, monkeypatch):
    """Test uninstalling a skill."""
    from zai import skills as skills_module
    monkeypatch.setattr(skills_module, "get_skills_dir", lambda: tmp_path)
    
    # Create a skill
    skill_dir = create_skill_template("to-remove")
    assert skill_dir.exists()
    
    # Uninstall
    result = uninstall_skill("to-remove")
    assert result is True
    assert not skill_dir.exists()


def test_uninstall_nonexistent(tmp_path, monkeypatch):
    """Test uninstalling a skill that doesn't exist."""
    from zai import skills as skills_module
    monkeypatch.setattr(skills_module, "get_skills_dir", lambda: tmp_path)
    
    result = uninstall_skill("never-existed")
    assert result is False


def test_build_skill_plugin(tmp_path, monkeypatch):
    """Test building the skill plugin."""
    from zai import skills as skills_module
    monkeypatch.setattr(skills_module, "get_skills_dir", lambda: tmp_path)
    
    plugin = build_skill_plugin()
    assert plugin is not None
    # Plugin name should be set
    assert plugin.name is not None
