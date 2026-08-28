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

    # --- SDK Sandbox (execution-level isolation) ---
    # See ``sandbox.py`` for the full mode matrix. ``host`` (default) means
    # no isolation — WorkspaceSandboxHook (security.py) provides the only
    # path-level enforcement. ``docker`` / ``ssh`` require the additional
    # fields below to be set.
    execution_sandbox: str = "host"
    sandbox_container: str = ""
    sandbox_container_workdir: str = ""
    sandbox_container_user: str = ""
    sandbox_ssh_host: str = ""
    sandbox_ssh_user: str = ""
    sandbox_ssh_port: int | None = None


_DEFAULT_TOOLS = (
    "read",
    "glob",
    "grep",
    "file_tree",
    "outline",
    "write",
    "edit",
)


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

    sandbox_mode = os.getenv("EXECUTION_SANDBOX", "host").strip().lower()

    def _opt_str(key: str) -> str:
        return os.getenv(key, "").strip()

    ssh_port_raw = os.getenv("SANDBOX_SSH_PORT", "").strip()
    ssh_port: int | None = int(ssh_port_raw) if ssh_port_raw else None

    return Config(
        ollama_base_url=base_url,
        ollama_model=model,
        ollama_auth_token=auth,
        agent_workspace=workspace,
        session_log_dir=log_dir,
        allowed_tools=allowed,
        execution_sandbox=sandbox_mode,
        sandbox_container=_opt_str("SANDBOX_CONTAINER"),
        sandbox_container_workdir=_opt_str("SANDBOX_CONTAINER_WORKDIR"),
        sandbox_container_user=_opt_str("SANDBOX_CONTAINER_USER"),
        sandbox_ssh_host=_opt_str("SANDBOX_SSH_HOST"),
        sandbox_ssh_user=_opt_str("SANDBOX_SSH_USER"),
        sandbox_ssh_port=ssh_port,
    )
