"""Streaming consumer for Strands ``agent.stream_async()``.

Strands emits **plain ``dict``** events (not dataclasses). This module
defines the ``StreamChunk`` shape (matches claude-agent's) and a
consumer that translates Strands dicts into ``StreamChunk`` instances.

Strands dict event shapes (per docs):

    {"data": "text chunk"}                              # text delta
    {"reasoningText": "..."}                             # thinking
    {"current_tool_use": {"toolUseId", "name", "input"}} # tool_use progress
    {"tool_stream_event": {"tool_use": ..., "data": ...}}
    {"message": {"role": "assistant"}}                   # new message node
    {"result": AgentResult}                              # final result
    {"force_stop": True, "force_stop_reason": "..."}      # aborted
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any


@dataclass
class StreamChunk:
    """Normalised streaming event — same shape as claude-agent's StreamChunk."""

    kind: str  # "text" | "thinking" | "tool_start" | "tool_input" | "tool_end" | "done"
    text: str = ""
    thinking: str = ""
    tool_name: str = ""
    tool_use_id: str = ""
    input_args: dict | None = None
    result: str = ""
    is_error: bool = False
    stop_reason: str | None = None
    usage: dict | None = None

    def format_tool_call(self) -> str:
        """Format tool call with args for display."""
        args_str = ""
        if self.input_args:
            args_list = []
            for k, v in self.input_args.items():
                v_str = str(v)
                if len(v_str) > 80:
                    v_str = v_str[:80] + "..."
                args_list.append(f"{k}={v_str!r}")
            args_str = "(" + ", ".join(args_list) + ")"
        return f"\n{self.tool_name}{args_str}"


class StreamConsumer:
    """Stateful translator from Strands dict events to StreamChunks.

    Tracks in-progress tool calls so that incremental
    ``current_tool_use`` updates can be emitted as ``tool_input``
    chunks (matching the schema of claude-agent's StreamChunk).

    Also exposes a side-channel queue for tool *results* (via
    ``register_tool_result``) so the caller can drain them alongside
    stream events — Strands does not emit a dedicated "tool_end" dict,
    so the AfterToolCallEvent hook is the authoritative source for
    tool results, and the consumer ferries them through here.
    """

    def __init__(self) -> None:
        self._pending_tools: dict[str, dict[str, Any]] = {}
        self._tool_result_queue: asyncio.Queue[StreamChunk] = asyncio.Queue()
        self.done_emitted = False

    def register_tool_result(
        self,
        tool_use_id: str,
        tool_name: str,
        result: str | Any,
        is_error: bool = False,
    ) -> StreamChunk:
        """Queue a ``tool_end`` chunk for the streaming loop to yield.

        Called from ``JsonlTraceHook.after_tool`` so the user sees the
        result inline (instead of waiting for a post-run JSONL summary).
        Returns the chunk that was queued (useful for tests).
        """
        chunk = StreamChunk(
            kind="tool_end",
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            result=str(result)[:5000] if result else "",
            is_error=is_error,
        )
        self._tool_result_queue.put_nowait(chunk)
        return chunk

    def drain_tool_results(self) -> list[StreamChunk]:
        """Drain all currently-queued tool_end chunks (non-blocking).

        Returns an empty list if nothing is pending. Order is FIFO.
        Each call empties the queue — a second drain returns ``[]``.
        """
        out: list[StreamChunk] = []
        while True:
            try:
                out.append(self._tool_result_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return out

    def feed(self, event: dict) -> list[StreamChunk]:
        """Translate one Strands dict event into zero or more StreamChunks."""
        out: list[StreamChunk] = []

        # --- text delta ---
        if "data" in event and isinstance(event["data"], str):
            out.append(StreamChunk(kind="text", text=event["data"]))

        # --- reasoning / thinking ---
        elif "reasoningText" in event:
            out.append(StreamChunk(kind="thinking", thinking=event["reasoningText"]))

        # --- current tool_use ---
        elif "current_tool_use" in event:
            ctu = event["current_tool_use"] or {}
            tool_use_id = str(ctu.get("toolUseId") or ctu.get("id") or "")
            name = str(ctu.get("name") or "")
            raw_input = ctu.get("input")
            # Strands accumulates tool input as a JSON string (e.g., '{"max_depth": 3}')
            # Parse it to extract arguments for display
            try:
                if isinstance(raw_input, str) and raw_input.strip():
                    input_args = json.loads(raw_input)
                    if not isinstance(input_args, dict):
                        input_args = {"value": input_args}
                elif isinstance(raw_input, dict):
                    input_args = raw_input
                else:
                    input_args = {}
            except (json.JSONDecodeError, TypeError):
                # Not JSON - treat as a command string (e.g., shell tool)
                input_args = {"command": raw_input} if raw_input else {}

            if name and tool_use_id not in self._pending_tools:
                # First time seeing this tool → emit tool_start
                self._pending_tools[tool_use_id] = {"name": name, "input": raw_input}
                out.append(
                    StreamChunk(
                        kind="tool_start",
                        tool_name=name,
                        tool_use_id=tool_use_id,
                        input_args=input_args,
                    )
                )
            elif tool_use_id in self._pending_tools:
                # Subsequent update → emit tool_input (incremental args)
                self._pending_tools[tool_use_id]["input"] = raw_input
                out.append(
                    StreamChunk(
                        kind="tool_input",
                        tool_name=self._pending_tools[tool_use_id]["name"],
                        tool_use_id=tool_use_id,
                        input_args=input_args,
                    )
                )

        # --- tool_stream_event (rare; intermediate tool progress) ---
        elif "tool_stream_event" in event:
            # Surface as tool_input with the latest snapshot for completeness.
            tse = event["tool_stream_event"] or {}
            inner = tse.get("tool_use") or {}
            tool_use_id = str(inner.get("toolUseId") or inner.get("id") or "")
            if tool_use_id and tool_use_id in self._pending_tools:
                self._pending_tools[tool_use_id]["input"] = inner.get("input") or {}
                out.append(
                    StreamChunk(
                        kind="tool_input",
                        tool_name=self._pending_tools[tool_use_id]["name"],
                        tool_use_id=tool_use_id,
                        input_args=inner.get("input") or {},
                    )
                )

        # --- new message node ---
        elif "message" in event and isinstance(event["message"], dict):
            # We just bump our internal num-turns counter via the
            # callback wrapper; no consumer-visible chunk.
            pass

        # --- final result ---
        elif "result" in event and not self.done_emitted:
            result = event["result"]
            # AgentResult fields vary by Strands version; pull defensively.
            final_text = _get_attr(result, "message", "") or ""
            metrics = _get_attr(result, "metrics", None)
            usage: dict | None = None
            if metrics is not None:
                inp = _get_attr(metrics, "input_tokens", 0) or 0
                out_t = _get_attr(metrics, "output_tokens", 0) or 0
                usage = {"input": inp, "output": out_t, "total": inp + out_t}
            stop_reason = str(_get_attr(result, "stop_reason", "end_turn") or "end_turn")
            is_error = bool(_get_attr(result, "stop_reason", "") == "error")
            self.done_emitted = True
            out.append(
                StreamChunk(
                    kind="done",
                    text=final_text,
                    result=final_text,
                    usage=usage,
                    stop_reason=stop_reason,
                    is_error=is_error,
                )
            )

        # --- force_stop ---
        elif event.get("force_stop") and not self.done_emitted:
            reason = event.get("force_stop_reason", "")
            self.done_emitted = True
            out.append(
                StreamChunk(
                    kind="done",
                    text=f"[force_stop] {reason}",
                    result=f"[force_stop] {reason}",
                    stop_reason="force_stop",
                    is_error=True,
                )
            )

        # --- Drain any pending tools (best-effort tool_end) ---
        # Strands does not emit a dedicated "tool_end" dict event; the
        # AfterToolCallEvent hook (registered by the caller) is the
        # authoritative source for tool results. We only emit a tool_end
        # here if the consumer explicitly asks for it.

        return out


def _get_attr(obj: Any, attr: str, default: Any = None) -> Any:
    """Compat helper: read attribute from object or dict."""
    if obj is None:
        return default
    if hasattr(obj, attr):
        return getattr(obj, attr)
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return default
