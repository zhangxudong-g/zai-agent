"""Snapshot serialization for session saves."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class Snapshot:
    """Session snapshot with full state."""

    name: str
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    config: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, str]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    VERSION = "1.0"

    def add_message(self, role: str, content: str) -> None:
        """Add a message to the snapshot."""
        self.messages.append({"role": role, "content": content})

    def add_tool_call(self, tool: str, args: dict, result: str) -> None:
        """Add a tool call record."""
        self.tool_calls.append({"tool": tool, "args": args, "result": result})

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "version": self.VERSION,
            "name": self.name,
            "created_at": self.created_at,
            "config": self.config,
            "messages": self.messages,
            "tool_calls": self.tool_calls,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Snapshot:
        """Create from dictionary."""
        return cls(
            name=data.get("name", "unknown"),
            created_at=data.get("created_at", ""),
            config=data.get("config", {}),
            messages=data.get("messages", []),
            tool_calls=data.get("tool_calls", []),
            metadata=data.get("metadata", {}),
        )


def save_snapshot(snapshot: Snapshot, path: Path) -> None:
    """Save snapshot to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(snapshot.to_dict(), f, indent=2, ensure_ascii=False)


def load_snapshot(path: Path) -> Snapshot:
    """Load snapshot from JSON file."""
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return Snapshot.from_dict(data)


def snapshot_from_jsonl(session_log: Path) -> Snapshot:
    """Parse a JSONL session log into a Snapshot."""
    name = session_log.stem
    snapshot = Snapshot(name=name)

    if not session_log.exists():
        return snapshot

    messages: list[dict] = []

    with session_log.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                record = json.loads(line.strip())
                event = record.get("event", "")

                if event == "user_message":
                    messages.append({"role": "user", "content": record.get("content", "")})
                elif event == "result_message":
                    if record.get("subtype") == "success":
                        messages.append({"role": "assistant", "content": record.get("content", "")})
                elif event == "tool_call_end":
                    snapshot.add_tool_call(
                        tool=record.get("tool_name", ""),
                        args=record.get("arguments", {}),
                        result=record.get("result", ""),
                    )
            except (json.JSONDecodeError, KeyError):
                continue

    snapshot.messages = messages
    return snapshot
