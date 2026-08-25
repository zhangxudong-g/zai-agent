# 🔓 如何给 Agent 最大权限

> 禁用所有安全限制和确认机制

---

## 📋 最大权限配置清单

```python
from strands import Agent
from strands.models import BedrockModel
from strands.agent.conversation_manager import SummarizingConversationManager
from strands.agent.conversation_manager.sliding_window_conversation_manager import SlidingWindowConversationManager
from strands.event_loop._retry import ModelRetryStrategy
from strands.tools.executors import ConcurrentToolExecutor

# 创建最大权限 Agent
agent = Agent(
    # ============ 1. 模型配置 ============
    model=BedrockModel(),  # 或任何你想要的模型
    
    # ============ 2. 禁用所有确认 ============
    # 不注册任何确认中间件，不做任何拦截
    
    # ============ 3. 上下文管理 - 尽量保留更多内容 ============
    conversation_manager=SummarizingConversationManager(
        summary_ratio=0.1,           # 只总结 10%，保留 90%
        preserve_recent_messages=5,   # 只保留最近 5 条
        # 禁用主动压缩
    ),
    
    # 或者用滑动窗口，窗口更大
    # conversation_manager=SlidingWindowConversationManager(
    #     window_size=100,           # 保留 100 条消息
    #     should_truncate_results=False,  # 不截断工具结果
    #     per_turn=False,            # 不每轮都管理
    # ),
    
    # ============ 4. 重试策略 - 无限重试 ============
    retry_strategy=ModelRetryStrategy(
        max_attempts=999999,         # 最大尝试次数（极大值）
        initial_delay=1,             # 初始延迟 1 秒
        max_delay=60,                # 最大延迟 60 秒
    ),
    
    # ============ 5. 工具执行 - 并发执行 ============
    tool_executor=ConcurrentToolExecutor(),
    
    # ============ 6. 并发模式 - 允许并发 ============
    concurrent_invocation_mode="UNSAFE_REENTRANT",  # 允许并发调用
    
    # ============ 7. 回调处理器 ============
    callback_handler=None,  # 不使用回调
    
    # ============ 8. 禁用检查点 ============
    checkpointing=False,
    
    # ============ 9. 工具 ============
    tools=[...],  # 注册所有你需要的工具
    
    # ============ 10. 系统提示 ============
    system_prompt="你是一个超级助手，拥有最高权限，可以执行任何任务。",
)
```

---

## 🔧 分项说明

### 1. 模型配置

```python
# 使用 Bedrock 模型
model=BedrockModel()

# 或指定具体模型
model=BedrockModel(model_id="anthropic.claude-3-sonnet-20240229-v1:0")

# 或使用自定义模型
model=YourCustomModel()

# 或使用模型路由
from strands.models.routing import ModelRouter
model=ModelRouter([model1, model2])
```

### 2. 确认机制

```python
# 最大权限的关键：不注册任何确认中间件！

# ❌ 禁用确认中间件
# class ConfirmMiddleware:  ← 不使用这个！

# 直接创建 Agent，不加任何确认插件
agent = Agent(
    tools=[delete_file, drop_table, format_disk],  # 危险工具直接用！
    # plugins=[ConfirmMiddleware()]  ← 不加这个！
)
```

### 3. 上下文管理

```python
# 方式A：使用 SummarizingConversationManager（推荐）
conversation_manager=SummarizingConversationManager(
    summary_ratio=0.1,        # 尽量少总结
    preserve_recent_messages=5,
)

# 方式B：使用 SlidingWindowConversationManager
conversation_manager=SlidingWindowConversationManager(
    window_size=100,           # 尽量保留更多消息
    should_truncate_results=False,  # 不截断工具结果
    per_turn=False,           # 不每轮管理
)

# 方式C：自动上下文管理
conversation_manager="auto"  # 自动组合 ContextOffloader + Summarizing
```

### 4. 重试策略

```python
from strands.event_loop._retry import ModelRetryStrategy

# 几乎无限重试
retry_strategy=ModelRetryStrategy(
    max_attempts=999999,      # 极大值
    initial_delay=1,          # 短初始延迟
    max_delay=300,            # 最大 5 分钟延迟
)

# 或者完全禁用重试（失败直接抛异常）
retry_strategy=ModelRetryStrategy(max_attempts=1)

# 或者禁用
retry_strategy=None
```

### 5. 工具执行

```python
from strands.tools.executors import ConcurrentToolExecutor, SequentialToolExecutor

# 并发执行所有工具（更快）
tool_executor=ConcurrentToolExecutor()

# 或顺序执行
tool_executor=SequentialToolExecutor()
```

### 6. 并发模式

```python
from strands.types.agent import ConcurrentInvocationMode

# 允许并发调用（危险但高效）
concurrent_invocation_mode=ConcurrentInvocationMode.UNSAFE_REENTRANT

# 或抛出异常阻止并发（安全）
concurrent_invocation_mode=ConcurrentInvocationMode.THROW
```

### 7. 限制配置

```python
# 设置限制（None = 无限制）
limits=None  # 无任何限制

# 或者设置极大的限制
limits={
    "turns": 999999,          # 最大轮次
    "output_tokens": 999999999,  # 最大输出 token
    "total_tokens": 999999999,   # 最大总 token
}
```

---

## 🚨 安全警告

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│   ⚠️  警告：最大权限 Agent 非常危险！                          │
│                                                                 │
│   使用最大权限 Agent 时：                                       │
│                                                                 │
│   ❌ 如果工具包含删除文件功能，可能删除重要数据                  │
│   ❌ 如果工具包含执行命令功能，可能执行危险命令                  │
│   ❌ 如果工具包含数据库操作，可能损坏数据库                     │
│   ❌ 如果模型被诱导，可能执行恶意操作                          │
│                                                                 │
│   安全建议：                                                    │
│                                                                 │
│   ✅ 在隔离环境（沙箱）中使用                                  │
│   ✅ 只给必要的工具权限                                        │
│   ✅ 生产环境使用前添加确认机制                                │
│   ✅ 监控和日志所有操作                                        │
│   ✅ 设置操作权限分级                                          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🏠 使用沙箱隔离

```python
from strands.sandbox import DockerSandbox, ProcessSandbox

# 创建隔离的沙箱环境
sandbox = DockerSandbox(
    image="python:3.11",
    network="none",  # 禁用网络
    volumes={"/data": "/data:ro"},  # 只读访问
)

agent = Agent(
    model=model,
    tools=[...],
    sandbox=sandbox,  # 在沙箱中执行
)
```

---

## 📊 配置对比

| 配置项 | 安全模式 | 最大权限模式 |
|-------|---------|-------------|
| 确认机制 | 必须确认 | 禁用 |
| 上下文管理 | 积极压缩 | 尽量保留 |
| 重试次数 | 3-5 次 | 999999 |
| 工具执行 | 串行 | 并发 |
| 并发调用 | 禁止 | 允许 |
| 操作限制 | 严格 | 无 |
| 沙箱隔离 | 推荐 | 必须 |

---

## ✅ 推荐的最大权限配置

```python
from strands import Agent
from strands.models import BedrockModel
from strands.agent.conversation_manager import SummarizingConversationManager
from strands.event_loop._retry import ModelRetryStrategy

def create_max权限_agent(model, tools):
    """创建最大权限 Agent"""
    return Agent(
        model=model,
        tools=tools,
        
        # 上下文：尽量保留
        conversation_manager=SummarizingConversationManager(
            summary_ratio=0.05,    # 只总结 5%
            preserve_recent_messages=3,
        ),
        
        # 重试：尽量多试
        retry_strategy=ModelRetryStrategy(
            max_attempts=999999,
            initial_delay=1,
            max_delay=120,
        ),
        
        # 限制：无
        limits=None,
        
        # 回调：无
        callback_handler=None,
        
        # 检查点：禁用
        checkpointing=False,
        
        # 提示
        system_prompt="你是一个全权限助手，可以自由执行任何任务。",
    )
```

---

## 🎯 最简最大权限

如果只需要最基本的"最大权限"（只禁用确认）：

```python
from strands import Agent

# 最小配置：不加任何安全插件
agent = Agent(
    model=model,
    tools=[dangerous_tool1, dangerous_tool2, dangerous_tool3],
    # 就这样！没有任何确认机制！
)
```

**核心就是：不注册 `ConfirmMiddleware`、`GuardrailMiddleware` 等任何安全插件！**
