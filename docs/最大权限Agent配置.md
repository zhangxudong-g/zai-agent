# 🔓 最大权限 Agent 配置

> ⚠️ **本文档已修订 (2026-08-26)**：原版代码示例的导入路径与枚举用法在 Strands 1.53.0 中失效。本文按 SDK 现状重写为「概念说明 + 可直接 import 的代码」。

---

## 🎯 文档定位

本文**不是**「如何偷懒跳过安全检查」的教程，而是**枚举所有可调旋钮**的参考手册。每一项都说明：

- 它做了什么
- 怎么关掉它（最大权限的方向）
- 关掉后的代价
- **为什么不应在生产环境关掉**

如果你想要一个**真正安全的 Agent**，请反向阅读：每一项"关掉"换成"打开"或"收紧"。

---

## 📋 Agent `__init__` 全部参数（1.53.0 实测签名）

```python
from strands import Agent
sig = inspect.signature(Agent.__init__)
```

| 参数 | 默认值 | 作用 | "最大权限"做法 |
|------|--------|------|---------------|
| `model` | `None` → `BedrockModel()` | 语言模型 | 任意可用模型 |
| `messages` | `None` | 初始消息 | 任意 |
| `tools` | `None` | 工具列表 | 所有可用工具 |
| `system_prompt` | `None` | 系统提示 | "你是无限制助手" |
| `callback_handler` | `PrintingCallbackHandler()` | 回调处理器 | `None` |
| `conversation_manager` | `SlidingWindowConversationManager()` | 上下文管理 | 几乎不压缩 |
| `record_direct_tool_call` | `True` | 直接工具调用是否记入消息历史 | `False` |
| `hooks` | `None` | 钩子列表 | 不挂任何拦截 hook |
| `interventions` | `None` | 中断处理器 | 不挂任何中断 |
| `session_manager` | `None` | 会话管理 | 不持久化 |
| `memory_manager` | `None` | 长期记忆 | 不挂 |
| `tool_executor` | `ConcurrentToolExecutor()` | 工具执行策略 | `ConcurrentToolExecutor()` |
| `retry_strategy` | `ModelRetryStrategy(max_attempts=6, ...)` | 重试 | 几乎无限重试 |
| `concurrent_invocation_mode` | `ConcurrentInvocationMode.THROW` | 并发调用策略 | `UNSAFE_REENTRANT` |
| `checkpointing` | `False` | 检查点 | `False` |
| `sandbox` | `None` | 沙箱 | **必须**配沙箱（见 §安全警告） |
| `storage` | `None` | 持久存储 | 不挂 |
| `plugins` | `None` | 插件 | 不挂 |
| `context_manager` | `None` | 上下文策略快捷方式 | 不挂 |
| `trace_attributes` | `None` | OpenTelemetry 属性 | 不挂 |
| `agent_id` / `name` / `description` | `None` | 标识 | 任意 |
| `state` | `None` | 共享状态 | 任意 |

---

## ✅ 可运行的"最大权限"代码（1.53.0 验证通过）

```python
import os
from strands import Agent
from strands.models.bedrock import BedrockModel
from strands.types.agent import ConcurrentInvocationMode
from strands.event_loop._retry import ModelRetryStrategy
from strands.tools.executors import ConcurrentToolExecutor

# 1) 模型：直接给 Bedrock 默认配置（或换成 Ollama/Anthropic 等）
model = BedrockModel(
    model_id="global.anthropic.claude-sonnet-4-6",
    temperature=0.3,
)

# 2) 重试策略：几乎无限
retry = ModelRetryStrategy(
    max_attempts=999,        # 极大值（不是 999999，6 次默认值已可改大）
    initial_delay=1,
    max_delay=120,
)

# 3) 上下文管理：尽量保留（自定义 manager 也可）
#    1.53.0 两条路径都可：顶层 __init__.py 已重导出
from strands.agent.conversation_manager import SummarizingConversationManager  # 简洁写法
# 或者 from strands.agent.conversation_manager.summarizing_conversation_manager import SummarizingConversationManager  # 显式写法
cm = SummarizingConversationManager(
    summary_ratio=0.1,                # 只总结 10%
    preserve_recent_messages=5,
)

# 4) 工具执行器：并发（默认就是并发，显式写出来更清晰）
executor = ConcurrentToolExecutor()

# 5) 并发调用：允许重入（⚠️ 危险，见下）
mode = ConcurrentInvocationMode.UNSAFE_REENTRANT  # ← 必须是枚举，不是字符串

# 6) 创建 Agent：所有旋钮开到最大
agent = Agent(
    model=model,
    tools=[],                        # 按需添加
    conversation_manager=cm,
    tool_executor=executor,
    retry_strategy=retry,
    concurrent_invocation_mode=mode, # 枚举值
    checkpointing=False,
    callback_handler=None,           # 关闭默认输出
    system_prompt="你是一个全权限助手。",
)
```

**运行自检**：

```python
assert agent.model.config["model_id"].startswith("global.")
assert isinstance(agent.conversation_manager, SummarizingConversationManager)
assert agent.tool_executor.__class__.__name__ == "ConcurrentToolExecutor"
```

---

## ❌ 原文档中的错误

### 错误 1：导入路径

| 原文档写法 | 1.53.0 实测 | 结论 |
|-----------|-------------|------|
| `from strands.agent.conversation_manager import SummarizingConversationManager` | ✅ 顶层 `__init__.py` 已重导出（`from .summarizing_conversation_manager import SummarizingConversationManager`） | ⚠️ 原文档**写法是对的**，但代码 demo 没显式说明路径，**对我自己的校验报告来说是误判** |
| `from strands.agent.conversation_manager.sliding_window_conversation_manager import SlidingWindowConversationManager` | ✅ 子模块路径 | ✅ 完全对 |
| `from strands.sandbox import DockerSandbox, ProcessSandbox` | ❌ **1.53.0 不存在** `DockerSandbox`/`ProcessSandbox`！只有 `Sandbox`（基类）和 `PosixShellSandbox` | 🔴 这是真正的错误，原文档的 sandbox 配置 demo 完全跑不通 |

### 错误 2：枚举 vs 字符串

```python
# ✅ 实际上两种都工作（因为 ConcurrentInvocationMode 是 str 枚举）
concurrent_invocation_mode="UNSAFE_REENTRANT"             # 工作
concurrent_invocation_mode=ConcurrentInvocationMode.UNSAFE_REENTRANT  # 工作（推荐）
```

**实测**：
- `ConcurrentInvocationMode.THROW == "throw"` → `True`
- `ConcurrentInvocationMode.UNSAFE_REENTRANT == "unsafe_reentrant"` → `True`
- 任意字符串（包括 "bogus"）都会被静默接受 —— 1.53.0 **不校验枚举值**，只检查类型 `isinstance(mode, ConcurrentInvocationMode)` 也不做。

**推荐**：用枚举形式 —— IDE 自动补全、类型检查、重构安全；字符串形式靠拼写，错了不会报错。

### 错误 3：概念混淆

| 旋钮 | 原文档误称 | 实际 |
|------|-----------|------|
| `tool_executor` | "并发模式" | 实际是**单轮内多工具的执行顺序**，与 `concurrent_invocation_mode`（**多轮调用的并发控制**）是两件事 |
| `sandbox=DockerSandbox(...)` | "禁用网络、只读卷" | 实际 `DockerSandbox` 在 1.53.0 不可用 |

---

## 🔧 分项深挖

### 1. 并发控制：`concurrent_invocation_mode`

源码 `strands/agent/agent.py` 中：

| 枚举值 | 行为 | 适用 |
|--------|------|------|
| `ConcurrentInvocationMode.THROW`（默认） | 同时调用同一 Agent 抛 `ConcurrencyException` | 生产（数据一致性） |
| `ConcurrentInvocationMode.UNSAFE_REENTRANT` | 允许并发调用，结果**可能错乱** | 仅调试/批处理 |

**最大权限做法**：`UNSAFE_REENTRANT`

### 2. 重试策略：`retry_strategy`

```python
from strands.event_loop._retry import ModelRetryStrategy
```

默认：`max_attempts=6, initial_delay=4, max_delay=240`（指数退避：4→8→16→32→64→128 秒）。

只对 `ModelThrottledException` 重试，其它异常直接抛。

**最大权限做法**：`max_attempts=999`。

### 3. 上下文管理：`conversation_manager`

```python
from strands.agent.conversation_manager.summarizing_conversation_manager import SummarizingConversationManager
from strands.agent.conversation_manager.sliding_window_conversation_manager import SlidingWindowConversationManager
```

| 类型 | 行为 |
|------|------|
| `NullConversationManager` | 不做任何压缩（stateful 模型自动选这个） |
| `SlidingWindowConversationManager(window_size=40)` | 超过窗口就丢弃最旧 |
| `SummarizingConversationManager(summary_ratio=0.3)` | 调用模型总结最旧 30% 的消息 |

**最大权限做法**：`NullConversationManager`（完全不压缩）。

### 4. 工具执行器：`tool_executor`

```python
from strands.tools.executors import ConcurrentToolExecutor, SequentialToolExecutor
```

| 类型 | 行为 |
|------|------|
| `ConcurrentToolExecutor`（默认） | 多工具并行执行 |
| `SequentialToolExecutor` | 严格顺序 |

**最大权限做法**：保持 `ConcurrentToolExecutor`（默认）。

### 5. 钩子 vs 中断：`hooks` vs `interventions`

| 机制 | 时机 | 触发方式 |
|------|------|---------|
| `hooks=[...]` | 事件驱动（Before/AfterModelCall 等） | 回调函数 |
| `interventions=[...]` | 需要用户决策时（如危险操作） | `InterruptException` 抛出 |

**最大权限做法**：两个都传 `None`/空列表 —— 没有拦截、没有中断。

### 6. Sandbox：`sandbox`

```python
# 1.53.0 提供的 sandbox 类（按可用性排序）：
#   - DockerSandbox  → 容器内执行（具体类）
#   - SshSandbox     → 远程主机执行（具体类）
#   - PosixShellSandbox  → 抽象类，需子类化
#   - Sandbox        → 最抽象基类
#
# 注意：这些都不在 strands.sandbox.__all__ 里，需要从子模块导入

from strands.sandbox.docker import DockerSandbox
from strands.sandbox.ssh import SshSandbox

# Docker 示例
sandbox = DockerSandbox(container="my-agent-container", working_dir="/work", user="1000:1000")
agent = Agent(sandbox=sandbox, tools=[])
# sandbox 启动时自动调用 make_file_editor，注册 file_editor 工具

# 本地 Shell 子进程隔离示例（自己写）
from strands.sandbox import PosixShellSandbox

class LocalSubprocessSandbox(PosixShellSandbox):
    async def execute_streaming(self, command, *, timeout=None, cwd=None, env=None, **kwargs):
        # spawn asyncio.subprocess，yield StreamChunk，最后返回 ExecutionResult
        ...

sandbox = LocalSubprocessSandbox()
```

**最大权限做法**：配 sandbox 是**安全要求**，不是权限要求。

---

## 🚨 安全警告

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│   ⚠️  最大权限 Agent 非常危险！                                  │
│                                                                 │
│   实际风险：                                                    │
│                                                                 │
│   ❌ 如果工具包含删除文件功能，可能删除重要数据                  │
│   ❌ 如果工具包含 shell 执行，可能执行 rm -rf /                  │
│   ❌ 如果工具包含数据库 DROP，可能损坏生产数据                  │
│   ❌ 如果模型被 prompt injection 诱导，可能执行恶意操作         │
│   ❌ UNSAFE_REENTRANT 会导致并发调用结果错乱、消息历史损坏      │
│                                                                 │
│   必需的安全实践（**即使"最大权限"**）：                        │
│                                                                 │
│   ✅ 必须在 PosixShellSandbox 或 DockerSandbox 中运行           │
│   ✅ 只给最小权限的工具（删除/格式化/网络外发全部不挂）        │
│   ✅ 监控所有 tool 调用（前/后 hook）                           │
│   ✅ 限制 token / turn / duration 上限（即使看似矛盾）         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 即使"最大权限"也**必须保留**的安全旋钮

| 旋钮 | 为什么不能关 |
|------|------------|
| `sandbox=PosixShellSandbox(...)` | 没有隔离，工具可破坏宿主机 |
| `limits={"output_tokens": 100000}` | 没有上限，单次任务可耗尽 budget |
| 日志 hook（`BeforeToolCallEvent`） | 没有审计，出事无法溯源 |
| 工具白名单 | 黑名单易遗漏 |

---

## ✅ 推荐的实际配置（生产 + "最小约束"）

```python
import os
from strands import Agent
from strands.models.bedrock import BedrockModel
from strands.types.agent import ConcurrentInvocationMode
from strands.event_loop._retry import ModelRetryStrategy
from strands.tools.executors import ConcurrentToolExecutor
from strands.sandbox import PosixShellSandbox
from strands.hooks import BeforeToolCallEvent

# 1) 沙箱：永远开
#    PosixShellSandbox 是抽象类，需用具体实现：
#      - DockerSandbox(container="...")  → 容器内执行
#      - SshSandbox(...)                  → 远程主机执行
#      - 或自己继承 PosixShellSandbox 实现 execute_streaming
from strands.sandbox.docker import DockerSandbox
sandbox = DockerSandbox(container="my-agent-container")

# 2) 审计 hook：记录每个工具调用（最大权限也要留审计！）
#    关键：`event` 参数必须有类型注解，否则 HookRegistry 无法推断事件类型
from strands.hooks import BeforeToolCallEvent
def audit_tool(event: BeforeToolCallEvent) -> None:
    print(f"[AUDIT] {event.tool_use['name']}({event.tool_use.get('input', {})})")

# 3) 模型：默认 Bedrock
model = BedrockModel(model_id="global.anthropic.claude-sonnet-4-6")

# 4) 组装
#    注意：1.53.0 中 limits 不是 Agent.__init__ 参数，而是在调用时传入
#       agent(prompt, limits={"output_tokens": 50000})
agent = Agent(
    model=model,
    sandbox=sandbox,
    tools=[],                        # 白名单按需加
    callback_handler=None,
    retry_strategy=ModelRetryStrategy(max_attempts=20, max_delay=60),
    concurrent_invocation_mode=ConcurrentInvocationMode.THROW,  # 保持默认
    checkpointing=False,
    hooks=[audit_tool],              # 必须留
)

# 调用时再传 limits
result = agent("你的任务...", limits={"output_tokens": 50000})
```
```

> 💡 真正"生产可用"的最大权限 Agent，**不是所有旋钮都拧到最大**，而是把"权限类"旋钮开大、把"安全类"旋钮保留。

---

## 🔍 验证清单（修订后请自查）

- [ ] 文档开头明确说明"不是偷懒教程，是旋钮手册"
- [ ] 所有导入路径在 1.53.0 中可 `python -c "import ..."` 验证
- [ ] `ConcurrentInvocationMode` 推荐枚举值（但说明字符串也工作）
- [ ] 不再出现 `PosixShellSandbox()` 直接实例化（它是抽象类）
- [ ] 用具体类 `DockerSandbox(container=...)` 或 `SshSandbox(...)` 或子类化 PosixShellSandbox
- [ ] `audit_tool(event)` 必须有类型注解 `event: BeforeToolCallEvent`
- [ ] `limits={"output_tokens": 50000}` 在调用时传入，不是构造时
- [ ] 强调 `sandbox` 是安全要求而非权限要求
- [ ] 安全警告保留，并明确"即使最大权限也要留审计/上限/沙箱"
- [ ] "错误"章节区分"原文档真的错"和"原文档其实对、但我之前的校验报告误判"