"""Project-level context loading (.zai/context.md, rules.md, ignore)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProjectContext:
    """Loaded project context."""
    
    context: str = ""
    rules: str = ""
    ignore_patterns: list[str] = field(default_factory=list)
    
    def is_empty(self) -> bool:
        """Check if all fields are empty."""
        return not (self.context or self.rules or self.ignore_patterns)
    
    def to_system_prompt(self) -> str:
        """Convert to system prompt addition."""
        parts = []
        
        if self.context:
            parts.append(f"## 项目上下文\n\n{self.context}")
        
        if self.rules:
            parts.append(f"## 项目规则\n\n{self.rules}")
        
        if not parts:
            return ""
        
        return "\n\n".join(parts)


class ContextLoader:
    """Load project context files from .zai/ directory."""
    
    ZAI_DIR = ".zai"
    CONTEXT_FILE = "context.md"
    RULES_FILE = "rules.md"
    IGNORE_FILE = "ignore"
    
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.zai_dir = workspace / self.ZAI_DIR
    
    def load(self) -> ProjectContext:
        """Load all context files."""
        ctx = ProjectContext()
        
        if not self.zai_dir.exists():
            return ctx
        
        # Load context.md
        context_path = self.zai_dir / self.CONTEXT_FILE
        if context_path.exists():
            try:
                ctx.context = context_path.read_text(encoding="utf-8").strip()
            except Exception:
                pass
        
        # Load rules.md
        rules_path = self.zai_dir / self.RULES_FILE
        if rules_path.exists():
            try:
                ctx.rules = rules_path.read_text(encoding="utf-8").strip()
            except Exception:
                pass
        
        # Load ignore patterns
        ignore_path = self.zai_dir / self.IGNORE_FILE
        if ignore_path.exists():
            try:
                ctx.ignore_patterns = self._parse_ignore(ignore_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        
        return ctx
    
    def _parse_ignore(self, content: str) -> list[str]:
        """Parse .zai/ignore file."""
        patterns = []
        for line in content.splitlines():
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue
            patterns.append(line)
        return patterns


def load_project_context(workspace: Path) -> ProjectContext:
    """Convenience function to load project context."""
    return ContextLoader(workspace).load()
