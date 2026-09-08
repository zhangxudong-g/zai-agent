"""Zai Agent - Local AI coding assistant powered by Ollama.

Public surface
==============

- :class:`Agent` \u2014 the main entry point (``Agent(config, logger).run(prompt)``)
- :class:`Config` / :func:`get_config` \u2014 environment-driven configuration
- :class:`StreamChunk` \u2014 streaming event shape
- :class:`SessionLogger` \u2014 JSONL session logger
- :func:`patch_ollama_thinking` \u2014 Strands monkey-patch for qwen3 reasoning
- :mod:`telemetry` \u2014 OpenTelemetry auto-activation from .env
- :mod:`paths` \u2014 Zai home directory management
"""

from . import telemetry
from .agent import Agent
from .config import Config, get_config
from .llm import patch_ollama_thinking
from .paths import (
    get_zai_config_dir,
    get_zai_env_file,
    get_zai_home,
    get_zai_log_dir,
    get_zai_sessions_dir,
    get_zai_workspace_dir,
    init_zai_config,
    print_zai_info,
)
from .stream import StreamChunk
from .trace import SessionLogger

__version__ = "0.1.0"

__all__ = [
    "Agent",
    "Config",
    "SessionLogger",
    "StreamChunk",
    "get_config",
    "get_zai_config_dir",
    "get_zai_env_file",
    "get_zai_home",
    "get_zai_log_dir",
    "get_zai_sessions_dir",
    "get_zai_workspace_dir",
    "init_zai_config",
    "patch_ollama_thinking",
    "print_zai_info",
    "telemetry",
]
