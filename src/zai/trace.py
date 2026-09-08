"""JSONL SessionLogger for the Strands PoC.

Mirrors the 13 event types of ``claude-agent/src/agent/session_logger.py``
so that ``claude-agent/src/agent/validate_session.py`` can validate the
output without modification. When we migrate for real we will reuse the
production ``SessionLogger`` from claude-agent directly.

Schema reference: see ``claude-agent/README.md`` "Session Log Format"
section — events: session_start / session_end / agent_start / agent_end /
user_prompt / tool_call_start / tool_call_end / tool_call_error / result /
error / message / session_summary.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class SessionLogger:
    """Minimal JSONL session logger compatible with the claude-agent schema."""

    def __init__(self, session_id: str, log_dir: Path):
        self.session_id = session_id
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / f"{session_id}.jsonl"
        self._sequence = 0
        self._file = None

    def _open(self):
        if self._file is None:
            self._file = self.log_file.open("a", encoding="utf-8")

    def _write(self, event: str, **extra: Any) -> dict:
        import json

        self._open()
        self._sequence += 1
        record = {
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.")
            + f"{datetime.now(UTC).microsecond // 1000:03d}Z",
            "session_id": self.session_id,
            "event": event,
            "sequence": self._sequence,
            "event_id": f"{int(time.time() * 1000) % 0xFFFFFFFF:08x}",
            **extra,
        }
        self._file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._file.flush()
        return record

    # --- lifecycle ---
    def session_start(self) -> dict:
        return self._write("session_start")

    def session_end(self) -> dict:
        return self._write("session_end")

    def agent_start(self, prompt: str) -> dict:
        return self._write("agent_start", prompt=prompt)

    def agent_end(self, message: str) -> dict:
        return self._write("agent_end", message=message)

    def user_prompt(self, prompt: str) -> dict:
        return self._write("user_prompt", prompt=prompt)

    def tool_call_start(self, *, tool_name: str, tool_call_id: str, arguments: dict) -> dict:
        return self._write(
            "tool_call_start",
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            arguments=arguments,
        )

    def tool_call_end(
        self, *, tool_call_id: str, result: str, tool_name: str | None = None
    ) -> dict:
        kwargs: dict[str, Any] = {"tool_call_id": tool_call_id, "result": str(result)[:5000]}
        if tool_name is not None:
            kwargs["tool_name"] = tool_name
        return self._write("tool_call_end", **kwargs)

    def tool_call_error(
        self, *, tool_call_id: str, error: str, tool_name: str | None = None
    ) -> dict:
        kwargs: dict[str, Any] = {"tool_call_id": tool_call_id, "error": str(error)[:5000]}
        if tool_name is not None:
            kwargs["tool_name"] = tool_name
        return self._write("tool_call_error", **kwargs)

    def result_message(
        self,
        *,
        subtype: str,
        is_error: bool,
        num_turns: int | None,
        duration_ms: int | None,
        stop_reason: str | None = None,
        sdk_session_id: str | None = None,
        usage: dict | None = None,
        total_cost_usd: float | None = None,
    ) -> dict:
        return self._write(
            "result",
            subtype=subtype,
            is_error=is_error,
            num_turns=num_turns,
            duration_ms=duration_ms,
            stop_reason=stop_reason,
            sdk_session_id=sdk_session_id,
            usage=usage,
            total_cost_usd=total_cost_usd,
        )

    def log_error(self, *, error: str, context: dict | None = None) -> dict:
        return self._write("error", error=error, context=context or {})

    def _write_message(self, content: str) -> dict:
        return self._write("message", content=content)

    def session_summary(
        self,
        *,
        duration_ms: int | None,
        model: str,
        tokens: dict | None,
        tool_calls: int = 0,
        tools: dict | None = None,
        errors: int = 0,
    ) -> dict:
        return self._write(
            "session_summary",
            duration_ms=duration_ms,
            model=model,
            tokens=tokens or {},
            tool_calls=tool_calls,
            tools=tools or {},
            errors=errors,
        )
