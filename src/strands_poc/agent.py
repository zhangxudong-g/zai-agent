"""Agent — Strands-backed general-purpose coding agent.

Public surface:

    Agent(config, logger).run(prompt)            -> str
    Agent(config, logger).run_async(prompt)       -> str (async)
    Agent(config, logger).run_streaming(prompt)   -> AsyncIterator[StreamChunk]
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from strands import Agent as StrandsAgent
from strands.handlers.callback_handler import null_callback_handler
from strands.hooks import AfterToolCallEvent

from . import telemetry
from .config import Config
from .llm import build_ollama_model_safe
from .sandbox import build_sandbox
from .security import WorkspaceSandboxHook
from .stream import StreamChunk, StreamConsumer
from .tools import build_tools
from .trace import SessionLogger

# 模块导入时自动启用 OTel 遥测（幂等，从 .env 读配置）。
telemetry.setup()


def _tool_name(tool: Any) -> str:
    """Return the registered name of a tool."""
    name = getattr(tool, "tool_name", None)
    if name:
        return str(name)
    name = getattr(tool, "__name__", None)
    return str(name) if name else ""


# --------------------------------------------------------------------- #
# JsonlTraceHook — single HookProvider that mirrors lifecycle into JSONL
# --------------------------------------------------------------------- #
class JsonlTraceHook:
    """HookProvider: writes tool_call_start / tool_call_end to JSONL and
    forwards tool results to the streaming ``StreamConsumer`` so the CLI
    renders them inline.
    """

    def __init__(self, logger: SessionLogger, consumer: StreamConsumer | None = None):
        self._logger = logger
        self._consumer = consumer

    def register_hooks(self, registry) -> None:  # type: ignore[no-untyped-def]
        registry.add_callback(AfterToolCallEvent, self.after_tool)

    def after_tool(self, event: AfterToolCallEvent) -> None:
        tool_use = (
            getattr(event, "tool_use", None) or getattr(event, "tool", None) or {}
        )
        if hasattr(tool_use, "get"):
            tool_name = str(tool_use.get("name") or "")
            tool_use_id = str(tool_use.get("toolUseId") or tool_use.get("id") or "")
        else:
            tool_name = str(getattr(tool_use, "name", "") or "")
            tool_use_id = str(
                getattr(tool_use, "toolUseId", "") or getattr(tool_use, "id", "") or ""
            )

        result = getattr(event, "result", None) or {}
        if hasattr(result, "get"):
            is_error = bool(result.get("status") == "error") or bool(
                getattr(event, "cancel_tool", None)
            )
            content = result.get("content", "")
        else:
            is_error = bool(getattr(event, "cancel_tool", None))
            content = result

        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(str(block.get("text", "")))
                    else:
                        parts.append(str(block))
                else:
                    parts.append(str(block))
            content = "\n".join(parts)

        if is_error:
            self._logger.tool_call_error(
                tool_call_id=tool_use_id,
                error=str(content)[:5000],
                tool_name=tool_name or None,
            )
        else:
            self._logger.tool_call_end(
                tool_call_id=tool_use_id,
                result=str(content)[:5000],
                tool_name=tool_name or None,
            )

        if self._consumer is not None:
            self._consumer.register_tool_result(
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                result=str(content)[:5000],
                is_error=is_error,
            )


# --------------------------------------------------------------------- #
# Agent
# --------------------------------------------------------------------- #
class Agent:
    """Strands-backed general-purpose coding agent.

    Args:
        config: Agent configuration
        logger: Session logger for JSONL output
    """

    SYSTEM_PROMPT = """你是一个代码助手。根据用户请求，使用可用的工具来完成任务。

Available tools:
- read: 读取文件内容，支持 offset/limit/max_bytes 参数
- glob: 按模式搜索文件路径
- grep: 在文件中搜索内容，支持 context/output_mode 参数
- file_tree: 获取目录结构（返回 JSON）
- outline: 提取 Python 文件中的类和函数签名
- shell: 执行 shell 命令（git, ls, find, grep, cat, tree 等）
- write: 写入文件
- edit: 编辑文件（diff 模式）

Guidelines:
- 优先使用 read/glob/grep 等工具探索代码
- 使用 shell 执行 git log、ls 等命令查看项目状态
- 写文件时使用 no_disturb=True 避免干扰现有代码
- 返回清晰、结构化的回答
- 如遇错误，提供有用的诊断信息
"""

    def __init__(
        self,
        config: Config,
        logger: SessionLogger,
    ):
        self.config = config
        self.logger = logger

        # Build tools
        self._tools = build_tools(config)

        # Streaming consumer shared between trace hook and run_streaming
        self._stream_consumer = StreamConsumer()

        # Register hooks
        self._sandbox_hook = WorkspaceSandboxHook(config.agent_workspace)
        self._trace_hook = JsonlTraceHook(logger, consumer=self._stream_consumer)

        # Build execution sandbox
        self._sandbox = build_sandbox(config)

        # Create Strands Agent
        self._inner = StrandsAgent(
            model=build_ollama_model_safe(config),
            tools=self._tools,
            system_prompt=self.SYSTEM_PROMPT,
            hooks=[self._sandbox_hook, self._trace_hook],
            sandbox=self._sandbox,
            callback_handler=null_callback_handler,
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def run(self, prompt: str) -> str:
        return asyncio.run(self.run_async(prompt))

    def _is_connection_error(self, exc: Exception) -> bool:
        """Check if exception is a connection error that should trigger retry."""
        error_msg = str(exc).lower()
        return any(
            keyword in error_msg
            for keyword in [
                "connect",
                "connection",
                "timeout",
                "cannot reach",
                "getaddrinfo",
                "network",
                "refused",
                "unreachable",
            ]
        )

    async def run_async(self, prompt: str, max_retries: int = 3) -> str:
        """Run the agent synchronously and return final text.

        Args:
            prompt: User prompt
            max_retries: Max retry attempts on connection errors (default: 3)
        """
        self.logger.session_start()
        self.logger.user_prompt(prompt)
        self.logger.agent_start(prompt)
        start_ms = int(time.time() * 1000)
        final_text: str | None = None
        is_error = False
        stop_reason = "end_turn"
        tokens: dict | None = None
        num_turns = 0
        last_error: Exception | None = None

        for attempt in range(max_retries):
            try:
                if hasattr(self._inner, "invoke_async"):
                    result = await self._inner.invoke_async(prompt)
                else:
                    result = await asyncio.to_thread(self._inner, prompt)
                if isinstance(result, str):
                    final_text = result
                else:
                    final_text = str(getattr(result, "message", None) or result or "")
                    metrics = getattr(result, "metrics", None)
                    if metrics is not None:
                        inp = getattr(metrics, "input_tokens", 0) or 0
                        out_t = getattr(metrics, "output_tokens", 0) or 0
                        tokens = {"input": inp, "output": out_t, "total": inp + out_t}
                    stop_reason = str(
                        getattr(result, "stop_reason", "end_turn") or "end_turn"
                    )
                    is_error = stop_reason == "error"
                last_error = None
                break
            except Exception as e:
                last_error = e
                if self._is_connection_error(e) and attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # 1s, 2s, 4s
                    print(f"[Retry {attempt + 1}/{max_retries}] Connection error: {e}")
                    print(f"  Waiting {wait_time}s before retry...")
                    await asyncio.sleep(wait_time)
                    continue
                is_error = True
                stop_reason = "exception"
                self.logger.log_error(error=str(e), context={"phase": "agent_run", "attempt": attempt + 1})
                raise
        else:
            # All retries exhausted
            if last_error:
                raise last_error

        self._finalize(
            start_ms, final_text, tokens, num_turns, is_error, stop_reason
        )

        return final_text or ""

    async def run_streaming(self, prompt: str, max_retries: int = 3) -> AsyncIterator[StreamChunk]:
        """Run the agent and yield ``StreamChunk`` events in real time.

        Args:
            prompt: User prompt
            max_retries: Max retry attempts on connection errors (default: 3)
        """
        self.logger.session_start()
        self.logger.user_prompt(prompt)
        self.logger.agent_start(prompt)
        start_ms = int(time.time() * 1000)
        consumer = self._stream_consumer
        final_text: str | None = None
        is_error = False
        stop_reason = "end_turn"
        tokens: dict | None = None
        num_turns = 0
        last_error: Exception | None = None

        for attempt in range(max_retries):
            try:
                iter_events = self._inner.stream_async(prompt)
                last_error = None
                break
            except Exception as e:
                last_error = e
                if self._is_connection_error(e) and attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    print(f"[Retry {attempt + 1}/{max_retries}] Connection error: {e}")
                    print(f"  Waiting {wait_time}s before retry...")
                    await asyncio.sleep(wait_time)
                    continue
                # Non-connection error or retries exhausted
                is_error = True
                stop_reason = "exception"
                self.logger.log_error(error=str(e), context={"phase": "agent_streaming", "attempt": attempt + 1})
                raise
        else:
            if last_error:
                raise last_error

        try:
            if iter_events is not None:
                async for event in iter_events:
                    if isinstance(event, dict):
                        for chunk in consumer.feed(event):
                            if chunk.kind == "text":
                                final_text = (final_text or "") + chunk.text
                            elif chunk.kind == "done":
                                final_text = chunk.result or final_text
                                tokens = chunk.usage or tokens
                                stop_reason = chunk.stop_reason or stop_reason
                                is_error = chunk.is_error or is_error
                                num_turns += 1
                            yield chunk
                    else:
                        yield StreamChunk(kind="done", result=str(event)[:5000])
                    for tool_chunk in consumer.drain_tool_results():
                        yield tool_chunk
                for tool_chunk in consumer.drain_tool_results():
                    yield tool_chunk
            else:
                result = await asyncio.to_thread(self._inner, prompt)
                if isinstance(result, str):
                    final_text = result
                else:
                    final_text = str(getattr(result, "message", None) or result or "")
                yield StreamChunk(kind="text", text=final_text or "")
                yield StreamChunk(
                    kind="done",
                    result=final_text or "",
                    stop_reason="end_turn",
                    is_error=False,
                )
        except Exception as e:
            is_error = True
            stop_reason = "exception"
            self.logger.log_error(error=str(e), context={"phase": "agent_streaming"})
            raise
        finally:
            self._finalize(
                start_ms, final_text, tokens, num_turns, is_error, stop_reason
            )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _finalize(
        self, start_ms, final_text, tokens, num_turns, is_error, stop_reason
    ) -> None:
        duration_ms = int(time.time() * 1000 - start_ms)
        self.logger.result_message(
            subtype="error_during_execution" if is_error else "success",
            is_error=is_error,
            num_turns=num_turns or None,
            duration_ms=duration_ms,
            stop_reason=stop_reason,
            sdk_session_id=None,
            usage=tokens,
            total_cost_usd=None,
        )
        self.logger.agent_end(final_text or "")
        self.logger.session_summary(
            duration_ms=duration_ms,
            model=self.config.ollama_model,
            tokens=tokens,
        )
        self.logger.session_end()
