"""Zai home directory management.

Unified paths for configuration, sessions, and workspace storage.
Defaults to ~/.zai/ but falls back to installation directory for portable setups.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def get_zai_home() -> Path:
    """Get the Zai home directory.
    
    Priority:
    1. $ZAI_HOME environment variable
    2. ~/.zai/ (user home - recommended)
    """
    if home := os.getenv("ZAI_HOME"):
        return Path(home).expanduser().resolve()
    
    user_home = Path.home()
    zai_home = user_home / ".zai"
    
    # Create directory if it doesn't exist
    zai_home.mkdir(parents=True, exist_ok=True)
    
    return zai_home


def get_zai_config_dir() -> Path:
    """Get the configuration directory (~/.zai/config/)."""
    config_dir = get_zai_home() / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_zai_sessions_dir() -> Path:
    """Get the sessions directory (~/.zai/sessions/)."""
    sessions_dir = get_zai_home() / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return sessions_dir


def get_zai_workspace_dir() -> Path:
    """Get the default workspace directory (~/.zai/workspace/)."""
    workspace_dir = get_zai_home() / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    return workspace_dir


def get_zai_log_dir() -> Path:
    """Get the log directory (~/.zai/logs/)."""
    log_dir = get_zai_home() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def get_zai_env_file() -> Path:
    """Get the default .env file path (~/.zai/config/.env)."""
    return get_zai_config_dir() / ".env"


def init_zai_config() -> Path:
    """Initialize default configuration file if not exists.
    
    Returns the path to the config file.
    """
    env_file = get_zai_env_file()
    
    if not env_file.exists():
        default_config = """# Zai Agent Configuration
# https://github.com/zhangxudong-g/zai-agent

# --- Ollama backend ---
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:1.7b

# --- Agent workspace (default) ---
AGENT_WORKSPACE=~/.zai/workspace

# --- Tool allow-list (comma-separated, empty = all) ---
# ALLOWED_TOOLS=

# --- Sandbox mode (host/docker/ssh) ---
EXECUTION_SANDBOX=host
"""
        env_file.write_text(default_config, encoding="utf-8")
    
    return env_file


def print_zai_info() -> None:
    """Print Zai home information."""
    home = get_zai_home()
    print(f"Zai Home: {home}")
    print(f"  Config: {get_zai_config_dir()}")
    print(f"  Sessions: {get_zai_sessions_dir()}")
    print(f"  Workspace: {get_zai_workspace_dir()}")
    print(f"  Logs: {get_zai_log_dir()}")
