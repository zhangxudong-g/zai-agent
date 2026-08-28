"""Community Tools Wrapper - 封装 strands-agents-tools 包中的工具。

提供统一的接口来配置和使用社区工具，方便维护和扩展。

工具分类:
- 基础工具 (base): calculator, current_time, sleep
- 文件操作 (file): file_read, file_write, editor
- 代码执行 (code): python_repl, code_interpreter (需要沙箱)
- Web/网络 (web): http_request, browser
- Shell (shell): shell, environment (Unix/Linux/Mac)
- AWS (aws): use_aws
- 记忆/Agent (agent): memory, graph, swarm, workflow

使用示例:

    from strands_poc.community_tools import build_community_tools

    # 构建所有工具
    tools = build_community_tools()

    # 只构建特定类别的工具
    tools = build_community_tools(categories=["base", "file"])

    # 构建单个工具
    tools = build_community_tools(names=["calculator", "current_time"])
"""

from __future__ import annotations

import os
import platform
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

# =============================================================================
# 工具映射表 - 定义所有可用工具
# =============================================================================

# 基础工具 (所有平台可用)
BASE_TOOLS = {
    "calculator": "strands_tools.calculator.calculator",
    "current_time": "strands_tools.current_time.current_time",
    "sleep": "strands_tools.sleep.sleep",
}

# 文件操作工具
FILE_TOOLS = {
    "file_read": "strands_tools.file_read.file_read",
    "file_write": "strands_tools.file_write.file_write",
}

# Web/网络工具
WEB_TOOLS = {
    "http_request": "strands_tools.http_request.http_request",
}

# Shell 工具 (Unix/Linux/Mac)
SHELL_TOOLS = {
    "shell": "strands_tools.shell.shell",
    "environment": "strands_tools.environment.environment",
}

# AWS 工具
AWS_TOOLS = {
    "use_aws": "strands_tools.use_aws.use_aws",
}

# 代码执行工具 (需要特殊依赖)
CODE_TOOLS = {
    "python_repl": "strands_tools.python_repl.python_repl",
}

# Agent/Workflow 工具
AGENT_TOOLS = {
    "graph": "strands_tools.graph.graph",
    "workflow": "strands_tools.workflow.workflow",
}

# RAG/Memory 工具
RAG_TOOLS = {
    "retrieve": "strands_tools.retrieve.retrieve",
}

# 所有工具映射
ALL_TOOLS = {
    **BASE_TOOLS,
    **FILE_TOOLS,
    **WEB_TOOLS,
    **SHELL_TOOLS,
    **AWS_TOOLS,
    **CODE_TOOLS,
    **AGENT_TOOLS,
    **RAG_TOOLS,
}

# 类别到工具的映射
CATEGORY_TO_TOOLS = {
    "base": BASE_TOOLS,
    "file": FILE_TOOLS,
    "web": WEB_TOOLS,
    "shell": SHELL_TOOLS,
    "aws": AWS_TOOLS,
    "code": CODE_TOOLS,
    "agent": AGENT_TOOLS,
    "rag": RAG_TOOLS,
}


# =============================================================================
# 工具配置
# =============================================================================

@dataclass
class ToolConfig:
    """单个工具的配置。"""
    name: str
    enabled: bool = True
    workspace: Path | None = None  # 文件操作工具的工作目录
    timeout: int | None = None  # 超时时间（秒）
    extra_params: dict | None = None  # 额外参数


@dataclass
class CommunityToolsConfig:
    """社区工具的整体配置。"""
    workspace: Path | None = None  # 默认工作目录
    bypass_consent: bool = True  # 跳过工具确认
    default_timeout: int = 60  # 默认超时时间
    enabled_tools: set[str] | None = None  # 启用的工具名称列表
    disabled_tools: set[str] | None = None  # 禁用的工具名称列表
    enabled_categories: set[str] | None = None  # 启用的类别
    disabled_categories: set[str] | None = None  # 禁用的类别

    def __post_init__(self):
        """设置默认值。"""
        if self.workspace is None:
            self.workspace = Path.cwd()

        # NOTE: BYPASS_TOOL_CONSENT is intentionally NOT set here.
        # Setting it as a config-construction side effect mutates global
        # process state and leaks across unrelated code paths. The flag
        # is applied by ``CommunityToolsBuilder`` at tool-load time
        # (see ``_apply_bypass_consent``).


# =============================================================================
# 工具构建器
# =============================================================================

class CommunityToolsBuilder:
    """社区工具构建器。"""

    def __init__(self, config: CommunityToolsConfig | None = None):
        self.config = config or CommunityToolsConfig()
        self._tool_cache: dict[str, object] = {}
        self._bypass_applied: bool = False

    def _apply_bypass_consent(self) -> None:
        """Set BYPASS_TOOL_CONSENT only when actually loading tools.

        The flag is required by ``strands_tools`` only when tools are
        loaded (not when config is constructed). Setting it here keeps
        the side effect scoped to the load step, so constructing a
        ``CommunityToolsConfig`` alone does not mutate global env state.
        Idempotent within this builder instance.
        """
        if self._bypass_applied:
            return
        if self.config.bypass_consent:
            os.environ["BYPASS_TOOL_CONSENT"] = "true"
        self._bypass_applied = True

    @staticmethod
    def _is_platform_compatible(tool_name: str) -> bool:
        """检查工具是否与当前平台兼容。"""
        if tool_name in SHELL_TOOLS and platform.system() == "Windows":
            # Shell 工具在 Windows 上不可用
            return False
        return True

    def _should_include_tool(self, tool_name: str) -> bool:
        """检查工具是否应该被包含。"""
        # 平台兼容性检查
        if not self._is_platform_compatible(tool_name):
            return False

        # 启用的工具列表检查
        if self.config.enabled_tools is not None:
            return tool_name in self.config.enabled_tools

        # 禁用的工具列表检查
        if self.config.disabled_tools is not None:
            return tool_name not in self.config.disabled_tools

        # 类别检查
        for category, tools in CATEGORY_TO_TOOLS.items():
            if tool_name in tools:
                if self.config.enabled_categories is not None:
                    return category in self.config.enabled_categories
                if self.config.disabled_categories is not None:
                    return category not in self.config.disabled_categories
                break

        return True

    def _load_tool(self, import_path: str) -> object:
        """动态加载工具。"""
        if import_path in self._tool_cache:
            return self._tool_cache[import_path]

        module_path, tool_name = import_path.rsplit(".", 1)
        import importlib
        module = importlib.import_module(module_path)
        tool = getattr(module, tool_name)
        
        self._tool_cache[import_path] = tool
        return tool

    def build(self) -> list[object]:
        """构建所有符合条件的工具列表。"""
        self._apply_bypass_consent()
        tools = []

        for tool_name, import_path in ALL_TOOLS.items():
            if self._should_include_tool(tool_name):
                try:
                    tool = self._load_tool(import_path)
                    tools.append(tool)
                except ImportError as e:
                    # 如果工具依赖缺失，记录警告但继续
                    import logging
                    logging.warning(f"Failed to load tool {tool_name}: {e}")
                    continue

        return tools

    def build_names(self, names: Sequence[str]) -> list[object]:
        """根据名称列表构建工具。"""
        self._apply_bypass_consent()
        tools = []

        for name in names:
            if name in ALL_TOOLS and self._should_include_tool(name):
                try:
                    tool = self._load_tool(ALL_TOOLS[name])
                    tools.append(tool)
                except ImportError as e:
                    import logging
                    logging.warning(f"Failed to load tool {name}: {e}")
                    continue

        return tools

    def build_categories(self, categories: Sequence[str]) -> list[object]:
        """根据类别列表构建工具。"""
        self._apply_bypass_consent()
        tools = []
        
        for category in categories:
            cat_tools = CATEGORY_TO_TOOLS.get(category, {})
            if not cat_tools:
                continue
                
            for tool_name in cat_tools:
                if self._should_include_tool(tool_name):
                    try:
                        tool = self._load_tool(ALL_TOOLS[tool_name])
                        tools.append(tool)
                    except ImportError as e:
                        import logging
                        logging.warning(f"Failed to load tool {tool_name}: {e}")
                        continue

        return tools


# =============================================================================
# 便捷函数
# =============================================================================

def build_community_tools(
    categories: Sequence[str] | None = None,
    names: Sequence[str] | None = None,
    workspace: Path | None = None,
    bypass_consent: bool = True,
    enabled_tools: Sequence[str] | None = None,
    disabled_tools: Sequence[str] | None = None,
    enabled_categories: Sequence[str] | None = None,
    disabled_categories: Sequence[str] | None = None,
) -> list[object]:
    """便捷函数：构建社区工具。

    Args:
        categories: 要包含的工具类别列表 (如 ["base", "file"])
        names: 要包含的特定工具名称列表 (如 ["calculator", "current_time"])
        workspace: 工作目录路径
        bypass_consent: 是否跳过工具确认提示
        enabled_tools: 只启用这些工具
        disabled_tools: 禁用这些工具
        enabled_categories: 只启用这些类别
        disabled_categories: 禁用这些类别

    Returns:
        工具实例列表

    Example:
        # 使用所有可用工具
        tools = build_community_tools()

        # 只使用基础工具
        tools = build_community_tools(categories=["base"])

        # 只使用特定工具
        tools = build_community_tools(names=["calculator", "current_time"])

        # 排除某些工具
        tools = build_community_tools(disabled_tools=["shell"])
    """
    config = CommunityToolsConfig(
        workspace=workspace,
        bypass_consent=bypass_consent,
        enabled_tools=set(enabled_tools) if enabled_tools else None,
        disabled_tools=set(disabled_tools) if disabled_tools else None,
        enabled_categories=set(enabled_categories) if enabled_categories else None,
        disabled_categories=set(disabled_categories) if disabled_categories else None,
    )

    builder = CommunityToolsBuilder(config)

    if names:
        return builder.build_names(names)
    elif categories:
        return builder.build_categories(categories)
    else:
        return builder.build()


def get_available_tools() -> dict[str, list[str]]:
    """获取所有可用的工具及其类别。

    Returns:
        类别到工具名称的映射
    """
    return {category: list(tools.keys()) for category, tools in CATEGORY_TO_TOOLS.items()}


def get_tools_summary() -> str:
    """获取工具摘要信息。

    Returns:
        格式化的工具摘要字符串
    """
    lines = ["Available Community Tools:"]
    lines.append("=" * 50)

    for category, tools in CATEGORY_TO_TOOLS.items():
        lines.append(f"\n[{category.upper()}]")
        for tool_name in tools:
            compatible = "[OK]" if CommunityToolsBuilder._is_platform_compatible(tool_name) else "[X]"
            lines.append(f"  {compatible} {tool_name}")

    return "\n".join(lines)
