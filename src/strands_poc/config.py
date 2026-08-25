"""Configuration for the Strands PoC agent harness.

Reads from environment variables (or a .env file) and produces a
``Config`` dataclass. Field set is intentionally close to the original
``claude-agent/src/agent/config.py`` so that downstream code (agent,
main) has a familiar surface.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Config:
    """Agent harness configuration."""

    # --- Required fields (no defaults) come first ---
    ollama_base_url: str
    ollama_model: str
    agent_workspace: Path
    session_log_dir: Path

    # --- Optional fields (with defaults) follow ---
    ollama_auth_token: str = "ollama"
    allowed_tools: list[str] = field(default_factory=list)


_DEFAULT_TOOLS = ("read", "glob", "grep", "write", "edit")


def get_config(env_file: str | Path | None = ".env") -> Config:
    """Build a Config from environment variables.

    Args:
        env_file: Path to a .env file to load. Set to ``None`` to skip
            loading and read only ``os.environ``.

    Returns:
        A populated ``Config`` instance.
    """
    if env_file is not None:
        load_dotenv(env_file)

    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model = os.getenv("OLLAMA_MODEL", "qwen3:7b")
    auth = os.getenv("OLLAMA_AUTH_TOKEN", "ollama") or "ollama"

    workspace = Path(os.getenv("AGENT_WORKSPACE", "./workspace/sample_project")).resolve()
    log_dir = Path(os.getenv("SESSION_LOG_DIR", "./sessions")).resolve()

    tools_raw = os.getenv("ALLOWED_TOOLS", ",".join(_DEFAULT_TOOLS))
    allowed = [t.strip().lower() for t in tools_raw.split(",") if t.strip()]

    return Config(
        ollama_base_url=base_url,
        ollama_model=model,
        ollama_auth_token=auth,
        agent_workspace=workspace,
        session_log_dir=log_dir,
        allowed_tools=allowed,
    )