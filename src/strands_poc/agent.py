"""Agent — Strands-backed coding-analysis agent.

Public surface mirrors ``claude-agent/src/agent/agent.py``:

    Agent(config, logger).run(prompt)            -> str
    Agent(config, logger).run_async(prompt)       -> str (async)
    Agent(config, logger).run_streaming(prompt)   -> AsyncIterator[StreamChunk]

Internally we assemble a ``strands.Agent`` with:

    - model = OllamaModel(host=config.ollama_base_url, model_id=config.ollama_model)
    - tools = build_tools(config)
    - hooks = [WorkspaceSandboxHook(workspace), JsonlTraceHook(logger)]
    - callback_handler = None  (we consume stream_async directly)
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from strands import Agent as StrandsAgent
from strands.hooks import AfterToolCallEvent

from . import telemetry
from .config import Config
from .llm import build_ollama_model_safe
from .security import WorkspaceSandboxHook
from .stream import StreamChunk, StreamConsumer
from .tools import build_tools
from .trace import SessionLogger

# 模块导入时自动启用 OTel 遥测（幂等，从 .env 读配置）。
telemetry.setup()

# 尝试导入社区工具（可选依赖）
try:
    from .community_tools import build_community_tools, CommunityToolsConfig
    COMMUNITY_TOOLS_AVAILABLE = True
except ImportError:
    COMMUNITY_TOOLS_AVAILABLE = False
    build_community_tools = None
    CommunityToolsConfig = None


# --------------------------------------------------------------------- #
# JsonlTraceHook — single HookProvider that mirrors lifecycle into JSONL
# --------------------------------------------------------------------- #
class JsonlTraceHook:
    """HookProvider: writes tool_call_start / tool_call_end to JSONL.

    Strands's AfterToolCallEvent carries ``tool_use`` and ``result`` dicts.
    We do *not* rely on session-level events here because the consumer
    loop (run_streaming) is responsible for writing session_start /
    agent_start / result / agent_end / session_end / session_summary.
    This split mirrors the ``claude-agent`` design where hook writes and
    main-loop writes are independent.
    """

    def __init__(self, logger: SessionLogger):
        self._logger = logger

    def register_hooks(self, registry) -> None:  # type: ignore[no-untyped-def]
        registry.add_callback(AfterToolCallEvent, self.after_tool)

    def after_tool(self, event: AfterToolCallEvent) -> None:
        # Strands's AfterToolCallEvent shape: tool_use + result
        tool_use = getattr(event, "tool_use", None) or getattr(event, "tool", None) or {}
        if hasattr(tool_use, "get"):
            tool_name = str(tool_use.get("name") or "")
            tool_use_id = str(tool_use.get("toolUseId") or tool_use.get("id") or "")
        else:
            tool_name = str(getattr(tool_use, "name", "") or "")
            tool_use_id = str(getattr(tool_use, "toolUseId", "") or getattr(tool_use, "id", "") or "")

        result = getattr(event, "result", None) or {}
        # Strands result is dict-like with content / status
        if hasattr(result, "get"):
            is_error = bool(result.get("status") == "error") or bool(getattr(event, "cancel_tool", None))
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


# --------------------------------------------------------------------- #
# Agent
# --------------------------------------------------------------------- #
class Agent:
    """Strands-backed code-analysis agent.

    Args:
        config: Agent configuration
        logger: Session logger for JSONL output
        use_community_tools: Whether to use strands-agents-tools community tools
            instead of custom tools. Default: False
        community_tool_categories: Categories of community tools to include
            (e.g., ["base", "file"]). Only used if use_community_tools=True.
        community_tool_names: Specific community tool names to include.
            Takes precedence over categories if provided.
    """

    def __init__(
        self,
        config: Config,
        logger: SessionLogger,
        use_community_tools: bool = False,
        community_tool_categories: list[str] | None = None,
        community_tool_names: list[str] | None = None,
    ):
        self.config = config
        self.logger = logger
        self._use_community_tools = use_community_tools

        # Build tools based on configuration
        if use_community_tools and COMMUNITY_TOOLS_AVAILABLE:
            # Use community tools
            if community_tool_names:
                self._tools = build_community_tools(names=community_tool_names)
            elif community_tool_categories:
                self._tools = build_community_tools(categories=community_tool_categories)
            else:
                # Default: use base + file tools
                self._tools = build_community_tools(categories=["base", "file"])
        else:
            # Use custom tools (default)
            self._tools = build_tools(config)

        # Register hooks (sandbox + JSONL mirror).
        self._sandbox_hook = WorkspaceSandboxHook(config.agent_workspace)
        self._trace_hook = JsonlTraceHook(logger)

        # Build system prompt
        system_prompt = self._build_system_prompt()

        # Strands Agent takes ``hooks=[HookProvider(), ...]`` at construction
        # time. Each provider's ``register_hooks(registry)`` is invoked
        # internally and explicitly binds events (BeforeToolCallEvent / etc.),
        # so we never need to call ``agent.add_hook(provider)`` directly
        # (that path requires type-annotated callbacks and rejects
        # bare HookProvider instances).
        self._inner = StrandsAgent(
            model=build_ollama_model_safe(config),
            tools=self._tools,
            system_prompt=system_prompt,
            hooks=[self._sandbox_hook, self._trace_hook],
        )

    def _build_system_prompt(self) -> str:
        """Build the 4-stage analysis protocol + structured output template.

        The same protocol is used regardless of which toolset is loaded;
        only the tool catalog line at the bottom changes.
        """
        use_community = getattr(self, "_use_community_tools", False)
        if use_community:
            tool_catalog = "你可以使用提供的任何工具，包括社区工具。"
        else:
            tool_catalog = (
                "Available tools: "
                "read (读文件, 支持 offset/limit/max_bytes), "
                "glob (搜索路径), "
                "grep (搜索内容, 支持 context/output_mode), "
                "file_tree (项目结构, 返回 JSON), "
                "outline (Python 文件的类/函数签名), "
                "write (写文件, 仅在无干扰模式下), "
                "edit (编辑文件, 仅在无干扰模式下)."
            )

        return (
            "你是一个严格的代码分析 Agent。请按以下 4 阶段协议工作。\n\n"
            "【分析协议】\n"
            "阶段1 Scope    — 先调用一次 file_tree（或 glob **/*）确认项目类型、目录边界。\n"
            "阶段2 Outline  — 对核心文件（一般 ≤ 5 个）调 outline 抽取类/函数签名。\n"
            "阶段3 Read     — 只读与问题相关的片段；用 read(offset, limit) 取局部，避免一次性读取大文件。\n"
            "阶段4 Synthesize — 用下列模板输出，禁止散文化，禁止空想。\n\n"
            "【输出模板（必须 4 段齐全，按顺序）】\n"
            "## 范围\n"
            "（简述分析对象的边界与判定）\n\n"
            "## 证据\n"
            "- file:line — 证据描述 N\n"
            "- file:line — 证据描述 N\n\n"
            "## 结论\n"
            "（基于证据的明确判断；如不确定，先在 ## 不确定性 写出理由再下结论）\n\n"
            "## 不确定性\n"
            "（列出尚未验证的假设；若无，写“无”）\n\n"
            "【自检规则】\n"
            "- 任何结论必须能指向 file:line；空泛表述（“可能”、“大概”、“一般认为”等）一律改为更精确措辞，或补一条证据。\n"
            "- 写文件前默认使用无干扰模式（no_disturb=True），除非用户明确要求覆盖或重写。\n\n"
            f"{tool_catalog}"
        )

    # ------------------------------------------------------------------ #
    # Self-check helpers (used by hooks and _finalize)
    # ------------------------------------------------------------------ #
    WEAK_ASSERTION_TOKENS = ("可能", "大概", "也许", "或许", "似乎", "应该", "我觉得", "我猜")

    @classmethod
    def detect_weak_assertions(cls, text: str) -> list[str]:
        """Return the weak-assertion tokens actually present in *text*.

        Cheap set intersection; "" or no-match → empty list. Used by the
        self-check pass to flag final answers that need stronger phrasing.
        """
        if not text:
            return []
        return [tok for tok in cls.WEAK_ASSERTION_TOKENS if tok in text]

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def run(self, prompt: str) -> str:
        return asyncio.run(self.run_async(prompt))

    async def run_async(self, prompt: str) -> str:
        """Run the agent synchronously (no streaming) and return final text."""
        self.logger.session_start()
        self.logger.user_prompt(prompt)
        self.logger.agent_start(prompt)
        start_ms = int(time.time() * 1000)
        final_text: str | None = None
        is_error = False
        stop_reason = "end_turn"
        tokens: dict | None = None
        num_turns = 0

        try:
            # Strands Agent is callable: agent(prompt) -> str
            # Use invoke_async if available, else fall back to sync call.
            if hasattr(self._inner, "invoke_async"):
                result = await self._inner.invoke_async(prompt)
            else:
                # Strands' Agent is sync; offload to a thread.
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
                stop_reason = str(getattr(result, "stop_reason", "end_turn") or "end_turn")
                is_error = stop_reason == "error"
        except Exception as e:
            is_error = True
            stop_reason = "exception"
            self.logger.log_error(error=str(e), context={"phase": "agent_run"})
            raise
        finally:
            self._finalize(start_ms, final_text, tokens, num_turns, is_error, stop_reason)

        return final_text or ""

    async def run_streaming(self, prompt: str) -> AsyncIterator[StreamChunk]:
        """Run the agent and yield ``StreamChunk`` events in real time."""
        self.logger.session_start()
        self.logger.user_prompt(prompt)
        self.logger.agent_start(prompt)
        start_ms = int(time.time() * 1000)
        consumer = StreamConsumer()
        final_text: str | None = None
        is_error = False
        stop_reason = "end_turn"
        tokens: dict | None = None
        num_turns = 0

        # Strands' stream_async is an async generator when stream=True.
        try:
            iter_events = self._inner.stream_async(prompt)
        except (TypeError, AttributeError):
            iter_events = None

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
                        # Unexpected shape — yield a done chunk as fallback.
                        yield StreamChunk(kind="done", result=str(event)[:5000])
            else:
                # Fallback path: no stream_async available.
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
            self._finalize(start_ms, final_text, tokens, num_turns, is_error, stop_reason)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _finalize(self, start_ms, final_text, tokens, num_turns, is_error, stop_reason) -> None:
        duration_ms = int((time.time() - start_ms / 1000) * 1000)
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