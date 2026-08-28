"""Strands Agents SDK PoC for agent harness migration.

Mirrors the structure of ``claude-agent/src/agent/`` but replaces the
``claude-agent-sdk`` subprocess transport with the in-process
``strands.Agent`` + ``WorkspaceSandboxHook`` (using
``BeforeToolCallEvent.cancel_tool``).

Public surface
==============

- :class:`Agent` \u2014 the main entry point (``Agent(config, logger).run(prompt)``)
- :class:`Config` / :func:`get_config` \u2014 environment-driven configuration
- :class:`StreamChunk` \u2014 streaming event shape (matches claude-agent's)
- :class:`SessionLogger` \u2014 JSONL session logger
- :func:`patch_ollama_thinking` \u2014 Strands monkey-patch for qwen3 reasoning
- :mod:`telemetry` \u2014 OpenTelemetry auto-activation from .env
"""

from . import telemetry
from .agent import Agent
from .config import Config, get_config
from .llm import patch_ollama_thinking
from .stream import StreamChunk
from .trace import SessionLogger

__version__ = "0.1.0"

__all__ = [
    "Agent",
    "Config",
    "SessionLogger",
    "StreamChunk",
    "get_config",
    "patch_ollama_thinking",
    "telemetry",
]
