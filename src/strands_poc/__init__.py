"""Strands Agents SDK PoC for agent harness migration.

Mirrors the structure of ``claude-agent/src/agent/`` but replaces the
``claude-agent-sdk`` subprocess transport with the in-process
``strands.Agent`` + ``WorkspaceSandboxHook`` (using
``BeforeToolCallEvent.cancel_tool``).
"""

from . import telemetry

__version__ = "0.1.0"

__all__ = ["telemetry"]
