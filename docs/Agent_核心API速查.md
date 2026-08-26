# 📚 Agent 核心 API 速查表（strands-agents 1.53.0）

> 最后更新：2026-08-26
> 验证版本：strands-agents 1.53.0
> 配套文档：Callback / Debug / AgentResult / Hooks（详见 docs/）

---

## 一、Agent 构造参数（全部 27 个）

| 参数 | 类型 | 默认 | 作用 |
|------|------|------|------|
| `model` | `Model \| str \| ModelRouter \| None` | `None` → `BedrockModel()` | 语言模型；字符串仅对 Bedrock 自动构造 |
| `messages` | `Messages \| None` | `None` | 初始消息列表 |
| `tools` | `list \| None` | `None` | 工具列表（字符串/字典/模块/AgentTool/ToolProvider） |
| `system_prompt` | `str \| list[SystemContentBlock] \| None` | `None` | 系统提示词 |
| `structured_output_model` | `type[BaseModel] \| None` | `None` | 强制 Pydantic 输出 |
| `callback_handler` | `Callable \| _DefaultCallbackHandlerSentinel \| None` | `PrintingCallbackHandler()` | 流式回调 |
| `conversation_manager` | `ConversationManager \| None` | `SlidingWindowConversationManager()` | 上下文管理 |
| `record_direct_tool_call` | `bool` | `True` | 直接工具调用是否记入消息历史 |
| `load_tools_from_directory` | `bool` | `False` | 从 `./tools 加载工具 |
| `trace_attributes` | `Mapping \| None` | `None` | OpenTelemetry 属性 |
| `agent_id` | `str \| None` | `None` | Agent 标识 |
| `name` | `str \| None` | `None` | 显示名 |
| `description` | `str \| None` | `None` | 描述 |
| `state` | `AgentState \| dict \| None` | `None` | 共享状态 |
| `context_manager` | `ContextManagerStrategy \| None` | `None` | 上下文策略快捷方式 |
| `plugins` | `list[Plugin] \| None` | `None` | 插件列表 |
| `hooks` | `list[HookProvider \| HookCallback] \| None` | `None` | 钩子列表 |
| `interventions` | `list[InterventionHandler] \| None` | `None` | 中断处理器 |
| `session_manager` | `SessionManager \| None` | `None` | 会话管理 |
| `memory_manager` | `MemoryManager \| MemoryManagerConfig \| None` | `None` | 长期记忆 |
| `structured_output_prompt` | `str \| None` | `None` | 结构化输出提示 |
| `tool_executor` | `ToolExecutor \| None` | `ConcurrentToolExecutor()` | 工具执行器 |
| `retry_strategy` | `ModelRetryStrategy \| _DefaultRetryStrategySentinel \| None` | `ModelRetryStrategy(max_attempts=6, max_delay=240, initial_delay=4)` | 重试策略 |
| `concurrent_invocation_mode` | `ConcurrentInvocationMode` | `THROW` | 并发调用策略 |
| `checkpointing` | `bool` | `False` | 检查点（实验性） |
| `sandbox` | `Sandbox \| None` | `None` | 沙箱环境 |
| `storage` | `Storage \| None` | `None` | 持久存储 |

---

## 二、Agent 公开方法

| 方法 | 用途 |
|------|------|
| `agent(prompt)` | 同步调用（`__call__`） |
| `agent(prompt, limits={...})` | 调用时传 limits（**不是构造参数**）|
| `await agent.invoke_async(prompt)` | 异步调用，返回 AgentResult |
| `async for event in agent.stream_async(prompt)` | 流式调用 |
| `agent.tool.tool_name(...)` | **直接调用工具**，绕过 LLM |
| `agent.as_tool()` | 把 Agent 包装成 Tool（用于多 Agent） |
| `agent.cancel()` | 取消当前调用 |
| `agent.hooks.add_callback(EventType, fn)` | 注册 hook |
| `agent.state.set/get/delete` | 跨调用状态（JSONSerializableDict） |

---

## 三、AgentResult 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `stop_reason` | `Literal["end_turn"\|"tool_use"\|"max_tokens"\|"limit_turns"\|"cancelled"\|"checkpoint"\|"interrupt"\|"error"]` | 停止原因 |
| `message` | `dict` (Message) | 最后一条 assistant 消息 |
| `metrics` | `EventLoopMetrics` | 性能+用量 |
| `state` | `dict` | 状态快照 |
| `interrupts` | `Sequence[Interrupt] \| None` | 中断列表 |
| `structured_output` | `BaseModel \| None` | Pydantic 输出 |
| `checkpoint` | `Checkpoint \| None` | 检查点 |
| `result.context_size` | property | 最近一次输入 token 数 |
| `result.projected_context_size` | property | 预计下轮 token 数 |

详见 `docs/AgentResult字段详解.md`。

---

## 四、EventLoopMetrics 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `cycle_count` | `int` | 总循环数 |
| `tool_metrics` | `dict[str, ToolMetrics]` | 每个工具的统计 |
| `cycle_durations` | `list[float]` | 每轮耗时（秒） |
| `agent_invocations` | `list[AgentInvocation]` | 每次调用的详情 |
| `traces` | `list[Trace]` | 链路追踪 |
| `accumulated_usage` | `Usage` | 累计 token（`inputTokens`/`outputTokens`/`totalTokens`）|
| `accumulated_metrics` | `Metrics` | 累计延迟（`latencyMs`）|

**ToolMetrics 字段**：`call_count`, `success_count`, `error_count`, `total_time`

详见 `docs/Debug日志与可观测性.md`。

---

## 五、Hook 事件类型

| 事件 | 触发时机 | 关键字段 |
|------|---------|---------|
| `AgentInitializedEvent` | Agent 构造完 | `agent` |
| `BeforeInvocationEvent` | `agent(prompt)` 前 | `agent`, `invocation_state` |
| `AfterInvocationEvent` | `agent(prompt)` 后 | `agent` |
| `BeforeModelCallEvent` | 调模型前 | `cancel`, `projected_input_tokens` |
| `AfterModelCallEvent` | 调模型后 | `retry`, `stop_response`, `exception` |
| `BeforeToolsEvent` | 工具批前 | `message`, `invocation_state` |
| `AfterToolsEvent` | 工具批后 | `message` |
| `BeforeToolCallEvent` | 单个工具前 | `cancel_tool`, `tool_use`, `selected_tool` |
| `AfterToolCallEvent` | 单个工具后 | `result`, `exception`, `duration` |
| `MessageAddedEvent` | 消息列表加新消息 | `message` |

详见 `docs/Hooks体系实战.md`。

---

## 六、HookOrder 预设值

| 名称 | 值 | 用途 |
|------|----|----|
| `SDK_FIRST` | `-100` | SDK 内置 hook（最先执行）|
| `INTERVENTION_OUTPUT` | `-90` | 干预输出 |
| `DEFAULT` | `0` | 用户默认 |
| `MODEL_ROUTING` | `50` | 模型路由 |
| `INTERVENTION_INPUT` | `90` | 干预输入 |
| `SDK_LAST` | `100` | SDK 内置 hook（最后执行）|

---

## 七、ConcurrentInvocationMode

| 枚举 | 行为 |
|------|------|
| `THROW`（默认）| 同时调用同一 Agent 抛 `ConcurrencyException` |
| `UNSAFE_REENTRANT` | 允许并发，结果可能错乱 |

⚠️ 1.53.0 接受字符串（如 `"unsafe_reentrant"`），但不校验拼写错误 —— 推荐用枚举。

---

## 八、关键类与导入路径

| 类 | 路径 |
|----|------|
| `Agent` | `strands.Agent` |
| `tool` | `strands.tool` |
| `AgentResult` | `strands.agent.agent_result.AgentResult` |
| `EventLoopMetrics` | `strands.telemetry.metrics.EventLoopMetrics` |
| `ConversationManager` | `strands.agent.conversation_manager.ConversationManager` |
| `SlidingWindowConversationManager` | `strands.agent.conversation_manager.SlidingWindowConversationManager` |
| `SummarizingConversationManager` | `strands.agent.conversation_manager.SummarizingConversationManager` |
| `ModelRetryStrategy` | `strands.event_loop._retry.ModelRetryStrategy` |
| `ConcurrentToolExecutor` | `strands.tools.executors.ConcurrentToolExecutor` |
| `SequentialToolExecutor` | `strands.tools.executors.SequentialToolExecutor` |
| `ConcurrentInvocationMode` | `strands.types.agent.ConcurrentInvocationMode` |
| `BedrockModel` | `strands.models.bedrock.BedrockModel` |
| `OllamaModel` | `strands.models.ollama.OllamaModel` |
| `file_editor` | `strands.vended_tools.file_editor`（vended，sandbox 用） |
| `DockerSandbox` | `strands.sandbox.docker.DockerSandbox` |
| `SshSandbox` | `strands.sandbox.ssh.SshSandbox` |
| `PosixShellSandbox` | `strands.sandbox.posix_shell.PosixShellSandbox`（抽象类） |
| `JSONSerializableDict` / `AgentState` | `strands.types.json_dict.JSONSerializableDict` |
| Hook 事件 | `strands.hooks.{Before,After}{Model,Tools,ToolCall,Invocation}Event` |
| `HookOrder` | `strands.hooks.HookOrder` |

---

## 九、常用 imports 模板

```python
# 核心
from strands import Agent, tool

#  模型
from strands.models.bedrock import BedrockModel
from strands.models.ollama import OllamaModel
# from strands.models.anthropic import AnthropicModel

# 工具（vended）
from strands.vended_tools import file_editor  # sandbox 场景
from strands.vended_tools import bash  # 推荐替代 calculator

# 工具（社区）
from strands_tools import calculator, current_time  # @tool 装饰过

# 上下文管理
from strands.agent.conversation_manager import (
    SlidingWindowConversationManager,
    SummarizingConversationManager,
)

# 重试 + 工具执行
from strands.event_loop._retry import ModelRetryStrategy
from strands.tools.executors import (
    ConcurrentToolExecutor,
    SequentialToolExecutor,
)

# 并发模式
from strands.types.agent import ConcurrentInvocationMode

# 钩子
from strands.hooks import (
    BeforeModelCallEvent, AfterModelCallEvent,
    BeforeToolCallEvent, AfterToolCallEvent,
    BeforeInvocationEvent, AfterInvocationEvent,
    MessageAddedEvent,
    HookOrder,
)

# 沙箱
from strands.sandbox.docker import DockerSandbox
from strands.sandbox.ssh import SshSandbox
```

---

## 十、决策流程图

```
我想要...                          →  用什么
─────────────────────────────────────────────────────────
让 Agent 跑一个任务                 →  agent(prompt)
看流式输出                          →  agent(prompt) + callback_handler
或者                               →  async for event in agent.stream_async(prompt)
直接调用某个工具（不调 LLM）        →  agent.tool.tool_name(...)
跨调用保存状态                       →  agent.state.set(k, v)
审计工具调用                         →  hooks: Before/AfterToolCallEvent
拒绝某个危险工具                     →  hooks: BeforeToolCallEvent.cancel_tool
修改模型输入                         →  hooks: BeforeModelCallEvent
强制模型重答                         →  hooks: AfterModelCallEvent.retry
超 token 上限自动取消                 →  hooks: BeforeModelCallEvent.cancel
UI 流式渲染                          →  callback_handler
AOP 风格多阶段组合                    →  middleware
限制上下文增长                       →  conversation_manager
自动重试 LLM                         →  retry_strategy
并发跑多个工具                       →  tool_executor=ConcurrentToolExecutor()
跑长任务（>几分钟）                  →  Agent(checkpointing=True)
隔离工具环境                         →  Agent(sandbox=DockerSandbox(...))
```

---

## 十一、与官方文档的差异清单

| 官方说法 | 1.53.0 实际 |
|---------|------------|
| `result.accumulated_usage` | ❌ 在 `result.metrics.accumulated_usage` |
| `result.tool_usage` | ❌ 在 `result.metrics.tool_metrics` |
| `result.total_cycles` | ❌ 在 `result.metrics.cycle_count` |
| `Agent(limits=...)` | ❌ `limits` 是调用参数，不是构造参数 |
| `from strands.agent.conversation_manager import SummarizingConversationManager` | ✅ 实际可用（顶层已重导出） |
| `concurrent_invocation_mode="UNSAFE_REENTRANT"` | ⚠️ 字符串能工作但不安全，推荐用枚举 |
| `from strands.sandbox import DockerSandbox` | ❌ 在 `strands.sandbox.docker` 子模块 |
| `PosixShellSandbox()` 直接实例化 | ❌ 抽象类，需用 `DockerSandbox`/`SshSandbox` 或子类化 |
| `from strands_tools import file_write` 当 Agent tool | ❌ `file_write` 是裸函数，需 `@tool` 装饰 |

---

## 十二、参考链接

- 官方 Quickstart：<https://strandsagents.com/docs/user-guide/quickstart/python/>
- 源码：`strands/agent/agent.py`（Agent 主类）
- 本地验证脚本：
  - `tests/demo_quickstart.py` — 官方 Quickstart 6 节
  - `tests/demo_callback.py` — 6 种 callback 模式
  - `tests/demo_debug.py` — 7 种日志/可观测性模式
  - `tests/demo_agent_result.py` — AgentResult 7 字段
  - `tests/demo_hooks.py` — 7 种 hook 事件
- 本地文档：
  - `docs/官方文档校验报告与学习路径.md`
  - `docs/Callback_Handler_实战.md`
  - `docs/Debug日志与可观测性.md`
  - `docs/AgentResult字段详解.md`
  - `docs/Hooks体系实战.md`
  - `docs/写文件工具无干扰模式配置.md`
  - `docs/最大权限Agent配置.md`