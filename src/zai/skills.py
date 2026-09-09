"""Skill plugin using Strands' AgentSkills.

Provides zai plugin integration with Strands' built-in skill system.
Skills are folders containing instructions that extend the agent's capabilities.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from strands.vended_plugins.skills import AgentSkills

from .paths import get_zai_home


def get_skills_dir() -> Path:
    """Get the user skills directory (~/.zai/plugins/skills/)."""
    skills_dir = get_zai_home() / "plugins" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    return skills_dir


def create_skill_template(name: str, description: str = "") -> Path:
    """Create a new skill from template.

    Args:
        name: Skill name (will be used as directory name)
        description: Short description of what the skill does

    Returns:
        Path to the created skill directory
    """
    skill_dir = get_skills_dir() / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    # Create SKILL.md with template
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        skill_md.write_text(
            f"""---
name: {name}
description: {description or f"Skill for {name}"}
---

# {name}

{description or f"Add your skill instructions here."}

## When to use this skill

Describe when the agent should activate this skill.

## Instructions

Step-by-step instructions for the agent when this skill is active.
""",
            encoding="utf-8",
        )

    return skill_dir


def list_skills() -> list[dict[str, Any]]:
    """List all available skills.

    Returns:
        List of dicts with skill info (name, description, path)
    """
    skills_dir = get_skills_dir()
    skills = []

    for path in sorted(skills_dir.iterdir()):
        if path.is_dir() and (path / "SKILL.md").exists():
            content = (path / "SKILL.md").read_text(encoding="utf-8")
            # Parse simple YAML frontmatter
            name = path.name
            description = ""

            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    frontmatter = parts[1].strip()
                    for line in frontmatter.splitlines():
                        if line.startswith("name:"):
                            name = line.split(":", 1)[1].strip()
                        elif line.startswith("description:"):
                            description = line.split(":", 1)[1].strip()

            skills.append({
                "name": name,
                "description": description,
                "path": str(path),
            })

    return skills


def install_skill(source: str | Path, name: str | None = None) -> Path:
    """Install a skill from a source path.

    Args:
        source: Path to the skill directory containing SKILL.md
        name: Optional name override (defaults to source directory name)

    Returns:
        Path to the installed skill directory
    """
    source = Path(source)
    if not source.is_dir():
        raise FileNotFoundError(f"Source not found: {source}")
    if not (source / "SKILL.md").exists():
        raise FileNotFoundError(f"SKILL.md not found in {source}")

    skill_name = name or source.name
    target = get_skills_dir() / skill_name

    if target.exists():
        raise FileExistsError(f"Skill already exists: {skill_name}")

    shutil.copytree(source, target)
    return target


def uninstall_skill(name: str) -> bool:
    """Uninstall a skill by name.

    Args:
        name: Skill name to uninstall

    Returns:
        True if skill was removed, False if it didn't exist
    """
    target = get_skills_dir() / name
    if target.exists() and target.is_dir():
        shutil.rmtree(target)
        return True
    return False


def build_skill_plugin() -> AgentSkills:
    """Build an AgentSkills plugin from the user's skills directory.

    Returns:
        AgentSkills plugin ready to be added to a Strands agent
    """
    skills_dir = get_skills_dir()
    return AgentSkills(
        skills=str(skills_dir),
        state_key="zai_agent_skills",
        max_resource_files=20,
        strict=False,
    )
