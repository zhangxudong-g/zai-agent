"""Safety interventions using Strands' InterventionHandler.

Provides:
- DangerousCommandIntervention: Confirms before running dangerous shell commands
- SensitiveFileIntervention: Blocks access to sensitive files (.env, .key, etc.)
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from strands.interventions import Confirm, Deny, Proceed
from strands.interventions.handler import InterventionHandler

if TYPE_CHECKING:
    from strands.hooks.events import BeforeToolCallEvent


# Patterns for dangerous shell commands
_DANGEROUS_PATTERNS = [
    # File deletion
    (r"\brm\s+(-[rf]+\s+)*[/\\]", "rm -rf (recursive force delete)"),
    (r"\brmdir\s+/[rs]?\b", "rmdir (recursive delete)"),
    # Disk operations
    (r"\b(dd|mkfs|fdisk|parted)\b", "disk operation tool"),
    # Permission changes
    (r"\bchmod\s+(-R\s+)?777\b", "chmod 777 (world-writable)"),
    (r"\bchown\s+-R\b", "chown -R (recursive ownership change)"),
    # Network downloads with execution
    (r"\bcurl\b.*\|\s*(bash|sh|python)", "pipe to shell from curl"),
    (r"\bwget\b.*\|\s*(bash|sh|python)", "pipe to shell from wget"),
    # System control
    (r"\b(shutdown|reboot|poweroff|halt)\b", "system power control"),
    # Process killing
    (r"\bkill\s+-9\s+1\b", "kill init (PID 1)"),
    (r"\bpkill\s+-9\b", "pkill -9 (force kill all)"),
    # Git dangerous operations
    (r"\bgit\s+push\s+(-f|--force)(?!-)", "git push --force"),
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard"),
    (r"\bgit\s+clean\s+(-fd|-f)\b", "git clean -f"),
]

# Sensitive file patterns (matched against tool args)
_SENSITIVE_FILE_PATTERNS = [
    r"\.env$",
    r"\.env\.",
    r"\.key$",
    r"\.pem$",
    r"id_rsa",
    r"id_dsa",
    r"\.htpasswd$",
    r"credentials",
    r"secret",
    r"\.aws/credentials",
]


class DangerousCommandIntervention(InterventionHandler):
    """Intervene on dangerous shell commands.

    Triggers:
    - Destructive commands (rm -rf, dd, etc.) → Confirm
    - Dangerous git operations → Confirm
    """

    name = "zai-dangerous-commands"

    def __init__(self, auto_approve: bool = False):
        """Initialize intervention.

        Args:
            auto_approve: If True, automatically approve dangerous commands.
        """
        super().__init__()
        self._auto_approve = auto_approve
        self._dangerous_patterns = [(re.compile(p), desc) for p, desc in _DANGEROUS_PATTERNS]

    def before_tool_call(self, event: BeforeToolCallEvent) -> Confirm | Proceed:
        """Check tool calls for dangerous operations."""
        if self._auto_approve:
            return Proceed()

        tool_name = getattr(event, "tool_use", None)
        if tool_name is None:
            return Proceed()

        # Get tool name
        name = ""
        if hasattr(tool_name, "get"):
            name = str(tool_name.get("name", ""))
        else:
            name = str(getattr(tool_name, "name", ""))

        if name != "shell":
            return Proceed()

        # Get command argument
        args = {}
        if hasattr(tool_name, "get"):
            args = tool_name.get("input", {}) or {}
            if not args:
                # Try direct args attribute
                args = tool_name.get("arguments", {}) or {}
        else:
            args = getattr(tool_name, "input", {}) or {}

        command = args.get("command", "")
        if not command:
            return Proceed()

        # Check for dangerous patterns
        for pattern, description in self._dangerous_patterns:
            if pattern.search(command):
                # Auto-deny on the most dangerous operations
                if description in ("system power control", "kill init (PID 1)"):
                    return Deny(reason=f"Blocked: {description}")

                # Confirm for other dangerous operations
                return Confirm(
                    reason=f"⚠️  Dangerous command detected: {description}\nCommand: {command[:200]}"
                )

        return Proceed()


class SensitiveFileIntervention(InterventionHandler):
    """Block access to sensitive files (.env, .key, credentials, etc.).

    Sensitive files are blocked by default - even read attempts are denied
    to prevent accidental leakage.
    """

    name = "zai-sensitive-files"

    def __init__(self, allow_read: bool = False):
        """Initialize intervention.

        Args:
            allow_read: If True, allow reads but block writes to sensitive files.
        """
        super().__init__()
        self._allow_read = allow_read
        self._patterns = [re.compile(p, re.IGNORECASE) for p in _SENSITIVE_FILE_PATTERNS]

    def _is_sensitive(self, path: str) -> bool:
        """Check if a path matches sensitive patterns."""
        return any(pattern.search(path) for pattern in self._patterns)

    def before_tool_call(self, event: BeforeToolCallEvent) -> Deny | Proceed:
        """Check tool calls for sensitive file access."""
        tool_use = getattr(event, "tool_use", None)
        if tool_use is None:
            return Proceed()

        # Get tool name and arguments
        if hasattr(tool_use, "get"):
            name = str(tool_use.get("name", ""))
            args = tool_use.get("input", {}) or {}
        else:
            name = str(getattr(tool_use, "name", ""))
            args = getattr(tool_use, "input", {}) or {}

        # Tools that operate on files
        file_tools = {"read", "write", "edit", "diff", "outline"}
        if name not in file_tools:
            return Proceed()

        # Check file path argument
        file_path = args.get("file_path", "") or args.get("path", "")
        if not file_path:
            return Proceed()

        if not self._is_sensitive(file_path):
            return Proceed()

        # Block writes always
        if name in {"write", "edit"}:
            return Deny(reason=f"🔒 Blocked write to sensitive file: {file_path}")

        # Block reads unless explicitly allowed
        if not self._allow_read:
            return Deny(reason=f"🔒 Blocked access to sensitive file: {file_path}")

        return Proceed()
