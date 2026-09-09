"""Zai Agent - Local AI coding assistant powered by Ollama.

Public surface
==============

- :class:`Agent` — the main entry point (``Agent(config, logger).run(prompt)``)
- :class:`Config` / :func:`get_config` — environment-driven configuration
- :class:`StreamChunk` — streaming event shape
- :class:`SessionLogger` — JSONL session logger
- :func:`patch_ollama_thinking` — Strands monkey-patch for qwen3 reasoning
- :mod:`telemetry` — OpenTelemetry auto-activation from .env
- :mod:`paths` — Zai home directory management
- :mod:`tui` — Terminal UI helpers (colors, spinners, boxes)
- :mod:`snapshot` — Session snapshot serialization
- :mod:`session_manager` — Session management (list/save/load/export)
- :mod:`context_loader` — Project context loading (.zai/context.md, rules.md, ignore)
"""

from . import telemetry, tui
from .agent import Agent
from .config import Config, get_config
from .context_loader import ContextLoader, ProjectContext, load_project_context
from .executors import build_concurrent_executor, build_sequential_executor, get_executor
from .interventions import DangerousCommandIntervention, SensitiveFileIntervention
from .llm import patch_ollama_thinking
from .skills import (
    build_skill_plugin,
    create_skill_template,
    install_skill,
    list_skills,
    uninstall_skill,
)
from .paths import (
    get_zai_config_dir,
    get_zai_env_file,
    get_zai_exports_dir,
    get_zai_home,
    get_zai_log_dir,
    get_zai_saves_dir,
    get_zai_sessions_dir,
    get_zai_workspace_dir,
    init_zai_config,
    print_zai_info,
)
from .session_manager import SessionManager, get_session_manager
from .snapshot import Snapshot, load_snapshot, save_snapshot
from .stream import StreamChunk
from .trace import SessionLogger

__version__ = "0.3.0"

__all__ = [
    "Agent",
    "Config",
    "ContextLoader",
    "DangerousCommandIntervention",
    "ProjectContext",
    "SensitiveFileIntervention",
    "SessionLogger",
    "SessionManager",
    "Snapshot",
    "StreamChunk",
    "build_concurrent_executor",
    "build_sequential_executor",
    "build_skill_plugin",
    "create_skill_template",
    "get_config",
    "get_executor",
    "get_zai_config_dir",
    "get_zai_env_file",
    "get_zai_exports_dir",
    "get_zai_home",
    "get_zai_log_dir",
    "get_zai_saves_dir",
    "get_zai_sessions_dir",
    "get_zai_workspace_dir",
    "get_session_manager",
    "init_zai_config",
    "install_skill",
    "list_skills",
    "load_project_context",
    "load_snapshot",
    "patch_ollama_thinking",
    "print_zai_info",
    "save_snapshot",
    "telemetry",
    "tui",
    "uninstall_skill",
]
