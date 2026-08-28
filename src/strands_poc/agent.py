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

# 尝试导入社区工具（可选依赖）
try:
    from .community_tools import CommunityToolsConfig, build_community_tools

    COMMUNITY_TOOLS_AVAILABLE = True
except ImportError:
    COMMUNITY_TOOLS_AVAILABLE = False
    build_community_tools = None
    CommunityToolsConfig = None


def _tool_name(tool: Any) -> str:
    """Return the registered name of a tool, regardless of its wrapping.

    Strands's ``@tool`` decorator returns a ToolSpec-like wrapper that
    exposes ``.tool_name``. Community tools (loaded by
    ``community_tools.build_community_tools``) are raw callables with no
    such attribute — fall back to ``__name__`` in that case. Returning an
    empty string when neither is available keeps callers (set lookups,
    dedupe checks) from raising on exotic objects.
    """
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
    renders them inline (rather than re-printing a JSONL summary later).

    Strands's AfterToolCallEvent carries ``tool_use`` and ``result`` dicts.
    We do *not* rely on session-level events here because the consumer
    loop (run_streaming) is responsible for writing session_start /
    agent_start / result / agent_end / session_end / session_summary.
    This split mirrors the ``claude-agent`` design where hook writes and
    main-loop writes are independent.
    """

    def __init__(self, logger: SessionLogger, consumer: StreamConsumer | None = None):
        self._logger = logger
        self._consumer = consumer  # optional: when set, tool_end chunks are pushed live

    def register_hooks(self, registry) -> None:  # type: ignore[no-untyped-def]
        registry.add_callback(AfterToolCallEvent, self.after_tool)

    def after_tool(self, event: AfterToolCallEvent) -> None:
        # Strands's AfterToolCallEvent shape: tool_use + result
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
        # Strands result is dict-like with content / status
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

        # Forward to the streaming consumer so the CLI sees tool results
        # in real time instead of waiting for a post-run JSONL re-print.
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
    """Strands-backed agent with two operating modes.

    Modes:
        "analysis"  — original 4-stage code-analysis protocol with 4-section
                      output (范围/证据/结论/不确定性). Read-only by default.
        "qa_fault"  — 5-stage fault-analysis protocol with 3-view HTML report
                      (直接原因/根本原因/修正方案). Auto-enables write+edit
                      so the model can persist its report.

    Args:
        config: Agent configuration
        logger: Session logger for JSONL output
        mode: Operating mode. Default: "qa_fault" (current customer scenario).
        use_community_tools: Whether to use strands-agents-tools community tools
            instead of custom tools. Default: False
        community_tool_categories: Categories of community tools to include
            (e.g., ["base", "file"]). Only used if use_community_tools=True.
        community_tool_names: Specific community tool names to include.
            Takes precedence over categories if provided.
    """

    VALID_MODES = ("analysis", "qa_fault")

    def __init__(
        self,
        config: Config,
        logger: SessionLogger,
        mode: str = "qa_fault",
        use_community_tools: bool = False,
        community_tool_categories: list[str] | None = None,
        community_tool_names: list[str] | None = None,
    ):
        if mode not in self.VALID_MODES:
            raise ValueError(
                f"invalid mode {mode!r}; must be one of {self.VALID_MODES}"
            )
        self.config = config
        self.logger = logger
        self._mode = mode
        self._use_community_tools = use_community_tools

        # Build tools based on configuration
        if use_community_tools and COMMUNITY_TOOLS_AVAILABLE:
            # Use community tools
            if community_tool_names:
                self._tools = build_community_tools(names=community_tool_names)
            elif community_tool_categories:
                self._tools = build_community_tools(
                    categories=community_tool_categories
                )
            else:
                # Default: use base + file tools
                self._tools = build_community_tools(categories=["base", "file"])
        else:
            # Use custom tools (default)
            self._tools = build_tools(config)

        # Mode-dependent tool policy: qa_fault enables write/edit so the model
        # can persist its HTML report; analysis keeps the read-only default.
        self._apply_mode_tool_policy()

        # Streaming consumer is shared between the JSONL trace hook (writes
        # here from AfterToolCallEvent) and run_streaming (drains here to
        # yield tool_end chunks inline — see run_streaming below).
        self._stream_consumer = StreamConsumer()

        # Register hooks (path-level sandbox + JSONL mirror + streaming tool_end push).
        # The SDK Sandbox (execution-level) is built separately and passed
        # via ``Agent(sandbox=...)`` below.
        self._sandbox_hook = WorkspaceSandboxHook(config.agent_workspace)
        self._trace_hook = JsonlTraceHook(logger, consumer=self._stream_consumer)

        # Build the SDK execution-level sandbox (host / posix / docker / ssh).
        # This is *separate* from WorkspaceSandboxHook — see sandbox.py for
        # the full rationale. Default mode = "host" (no isolation, zero deps).
        self._sandbox = build_sandbox(config)

        # Build system prompt
        system_prompt = self._build_system_prompt(mode)

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
            sandbox=self._sandbox,
            callback_handler=null_callback_handler,
        )

    def _build_system_prompt(self, mode: str | None = None) -> str:
        """Build the system prompt for the given operating mode.

        Args:
            mode: One of ``"analysis"`` or ``"qa_fault"``. Defaults to
                ``self._mode`` when not supplied, so the production code
                path (``__init__`` → ``_build_system_prompt(mode)``) passes
                the mode explicitly and tests can drive either branch.
        """
        effective_mode = mode or getattr(self, "_mode", "qa_fault")
        if effective_mode == "qa_fault":
            return self._build_qa_fault_prompt()
        return self._build_analysis_prompt()

    def _build_analysis_prompt(self) -> str:
        """Original 4-stage code-analysis protocol + 4-section output template.

        Used by ``mode="analysis"``. The same protocol is used regardless of
        which toolset is loaded; only the tool catalog line at the bottom
        changes.
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

    def _build_qa_fault_prompt(self) -> str:
        """5-stage fault-analysis protocol + 3-view HTML report template.

        Used by ``mode="qa_fault"``. Designed for customer scenarios like
        ``prompts/qa001_concurrent_edit_analysis.txt``: input is a QA-style
        fault description (故障信息), output is a self-contained HTML file
        persisted via the ``write`` tool.
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
                "write (写文件，写 HTML 报告到 workspace), "
                "edit (编辑文件)."
            )

        # HTML skeleton the model is expected to complete. Kept inline so the
        # prompt stays self-contained; the model only fills in body content
        # between the marked placeholders.
        html_skeleton = (
            "<!DOCTYPE html>\n"
            '<html lang="zh-CN">\n'
            "<head>\n"
            '  <meta charset="UTF-8">\n'
            "  <title>QA 故障分析报告</title>\n"
            "  <style>\n"
            "    body { font-family: -apple-system, sans-serif; line-height: 1.6;\n"
            "           max-width: 900px; margin: 2em auto; padding: 0 1em;\n"
            "           color: #222; }\n"
            "    h1 { border-bottom: 2px solid #333; padding-bottom: .3em; }\n"
            "    h2 { margin-top: 2em; color: #1a1a8c; }\n"
            "    h3 { margin-top: 1.2em; color: #555; }\n"
            "    pre { background: #f4f4f4; padding: .8em; border-radius: 4px;\n"
            "          overflow-x: auto; }\n"
            "    code { font-family: 'Fira Code', monospace; font-size: .95em; }\n"
            "    .meta { color: #666; font-size: .9em; }\n"
            "  </style>\n"
            "</head>\n"
            "<body>\n"
            "  <!-- BEGIN BODY -->\n"
            "  <!-- END BODY -->\n"
            "</body>\n"
            "</html>\n"
        )

        return (
            "你是一个严格的 QA 故障分析 Agent。请按以下 5 阶段协议工作。\n\n"
            "【分析协议】\n"
            "阶段1 Scope     — 先调用一次 file_tree（或 glob **/*）确认项目类型、目录边界。\n"
            "阶段1.5 Diagnose — 从 QA 描述中抽取：故障现象 / 关键名词 / 怀疑模块 / 调用链假设。\n"
            "阶段2 Outline   — 对核心文件（一般 ≤ 5 个）调 outline 抽取类/函数签名。\n"
            "阶段3 Read      — 只读与故障相关的片段；用 read(offset, limit) 取局部。\n"
            "阶段4 Synthesize — 用下列模板输出完整 HTML 报告，并 write 到 workspace。\n\n"
            "【输出模板（必须 3 视角齐全，按顺序；全部中文）】\n"
            "## 直接原因（程序层面）\n"
            "・是哪个处理环节 / 哪段逻辑产生了误判定\n"
            "・并发控制 / 边界判断 / 异常处理的具体问题点\n"
            "・对应源代码（类名、函数名、文件名）显示完整路径 + file:line\n\n"
            "## 根本原因（设计与流程层面）\n"
            "・为何会发生该问题\n"
            "・为何在事前阶段未能被识别\n"
            "・设计前提或业务规格上的认知偏差\n\n"
            "## 修正方案（附变更前后对比）\n"
            "(1) 变更前逻辑 — 对应代码（含 file:line 注释）\n"
            "(2) 变更后逻辑 — 对应代码（含 file:line 注释）\n"
            "(3) 变更理由\n\n"
            "【HTML 输出要求】\n"
            f"把上面的 3 视角内容填入下面的 HTML 骨架的 BEGIN BODY / END BODY 之间，"
            f"然后用 write 工具把完整 HTML 写到 workspace：\n\n"
            f"{html_skeleton}\n\n"
            "【自检规则】\n"
            "- 任何结论必须能指向 file:line；空泛表述（“可能”、“大概”、“一般认为”等）一律改写或补一条证据。\n"
            "- HTML 必须闭合：检查 </body></html> 收尾，且所有 <h*> / <pre> / <code> 配对。\n"
            '- before/after 代码块用 <pre><code class="language-java"> 包裹；保留 file:line 注释。\n'
            "- 全文中文输出！\n\n"
            f"{tool_catalog}"
        )

    def _apply_mode_tool_policy(self) -> None:
        """Adjust ``self._tools`` according to the current operating mode.

        ``qa_fault`` mode appends ``write`` and ``edit`` tools so the model
        can persist its HTML report. ``analysis`` mode **strips** write
        and edit from the tool list — even if the user has them in
        ``ALLOWED_TOOLS`` — to enforce the docstring's "read-only by
        default" contract. Without this, an env config of
        ``ALLOWED_TOOLS=read,write,edit`` would silently leak write
        capability into a mode that promises to be read-only.

        When ``use_community_tools=True`` the user has opted into the
        community-tool ecosystem (``strands-agents-tools``). Mixing custom
        write/edit tools into that set would silently override their
        selection (``file_write`` / ``editor``), so this method is a
        no-op for community mode — the user picks persistence tools via
        ``--community-tool-names``.
        """
        if self._mode != "qa_fault":
            # analysis mode: enforce read-only by stripping write/edit.
            self._tools = [
                t for t in self._tools if _tool_name(t) not in ("write", "edit")
            ]
            return
        if getattr(self, "_use_community_tools", False):
            # User opted into community tools; do not inject custom ones.
            return

        existing = {_tool_name(t) for t in self._tools}
        from .tools import make_edit_tool, make_write_tool

        if "write" not in existing:
            self._tools.append(make_write_tool(self.config.agent_workspace))
        if "edit" not in existing:
            self._tools.append(make_edit_tool(self.config.agent_workspace))


    # ------------------------------------------------------------------ #
    # Self-check helpers (used by tests/test_agent_optimizations.py)
    # ------------------------------------------------------------------ #
    WEAK_ASSERTION_TOKENS = (
        "可能",
        "大概",
        "也许",
        "或许",
        "似乎",
        "应该",
        "我觉得",
        "我猜",
    )

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
        # Both user_prompt AND agent_start are emitted so the JSONL log
        # matches the 9-event sequence the ``claude-agent`` schema and
        # ``tests/test_smoke.py::test_session_logger_writes_well_formed_jsonl``
        # expect. user_prompt is the human-input audit row; agent_start
        # is the start-of-processing row. They carry the same payload
        # today but have different semantic meaning downstream.
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
                stop_reason = str(
                    getattr(result, "stop_reason", "end_turn") or "end_turn"
                )
                is_error = stop_reason == "error"
        except Exception as e:
            is_error = True
            stop_reason = "exception"
            self.logger.log_error(error=str(e), context={"phase": "agent_run"})
            raise
        finally:
            self._finalize(
                start_ms, final_text, tokens, num_turns, is_error, stop_reason
            )

        return final_text or ""

    async def run_streaming(self, prompt: str) -> AsyncIterator[StreamChunk]:
        """Run the agent and yield ``StreamChunk`` events in real time."""
        self.logger.session_start()
        # See run_async for why both user_prompt and agent_start fire.
        self.logger.user_prompt(prompt)
        self.logger.agent_start(prompt)
        start_ms = int(time.time() * 1000)
        # Use the shared consumer wired into JsonlTraceHook — tool results
        # arrive there via AfterToolCallEvent, and we drain them below.
        consumer = self._stream_consumer
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
                    # Drain any tool_end chunks the AfterToolCallEvent hook
                    # pushed into consumer._tool_result_queue between Strands
                    # events. Yields them inline so the user sees tool results
                    # in real time (no post-run JSONL re-print needed).
                    for tool_chunk in consumer.drain_tool_results():
                        yield tool_chunk
                # Final drain: hooks may have fired after the last Strands event.
                for tool_chunk in consumer.drain_tool_results():
                    yield tool_chunk
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
