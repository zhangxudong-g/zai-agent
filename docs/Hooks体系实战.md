# 🎣 Hooks 体系实战

> 配套代码：`tests/demo_hooks.py`（7 种模式可一键复现）
> 验证时间：2026-08-26 / strands-agents 1.53.0
> 验证方式：`python tests/demo_hooks.py --all`

---

## 一、Hooks vs Callback Handler vs Middleware

Strands 1.53.0 有 3 种"插入逻辑"的机制：

| 机制 | 时机 | 签名 | 用途 |
|------|------|------|------|
| **callback_handler** | 每个流式 chunk | `def cb(**kwargs)` | UI 流式渲染 |
| **hooks** | 强类型事件 | `def cb(event: EventType)` | 拦截、审计、修改 |
| **middleware** | 阶段化（AOP 风格）| `async def mw(ctx, next_fn)` | 复杂编排（如 retry 链）|

**经验法则**：
- 90% 需求 → **hooks**
- UI 流式 → callback_handler
- 多阶段组合 → middleware

---

## 二、7 种 Hook 事件速查表

| Hook | 触发时机 | 关键字段 | 用途 |
|------|---------|---------|------|
| `BeforeModelCallEvent` | 调模型前 | `cancel`, `projected_input_tokens` | 取消、改 projected tokens |
| `AfterModelCallEvent` | 调模型后 | `retry`, `stop_response`, `exception` | 触发重试、读结果 |
| `BeforeToolCallEvent` | 调工具前 | `cancel_tool`, `tool_use`, `selected_tool` | 拒绝、改参数 |
| `AfterToolCallEvent` | 调工具后 | `result`, `exception`, `duration` | 改结果、retry |
| `BeforeInvocationEvent` | `agent(prompt)` 前 | `agent`, `invocation_state` | 会话级前置 |
| `AfterInvocationEvent` | `agent(prompt)` 后 | `agent`, `stop_reason` | 会话级后置 |
| `MessageAddedEvent` | messages 列表加新消息 | `message` | 监听历史 |
| `AgentInitializedEvent` | Agent 构造完 | `agent` | 初始化检查 |

---

## 三、demo 运行结果（实测）

```text
[Pattern 1] BeforeModelCallEvent - 取消          PASS (16.1s)
[Pattern 2] AfterModelCallEvent - 重试            PASS (2.7s)
[Pattern 3] BeforeToolCallEvent - 校验            PASS (34.1s)
[Pattern 4] AfterToolCallEvent - 修改结果         PASS (2.7s)
[Pattern 5] Invocation 事件                       PASS (2.7s)
[Pattern 6] MessageAddedEvent                     PASS (34.0s)
[Pattern 7] HookOrder 优先级                      PASS (1.8s)
结果：7/7 passed
```

---

## 四、关键 API

### 4.1 注册 hook（避开嵌套函数坑）

```python
from strands.hooks import BeforeModelCallEvent


# ✅ 推荐：模块顶层函数 + 显式 add_callback
def on_before(event: BeforeModelCallEvent) -> None:
    event.cancel = True  # 取消本次模型调用


agent = Agent(model=..., tools=...)
agent.hooks.add_callback(BeforeModelCallEvent, on_before)

# ✅ 也可以：Hooks 列表传入（需顶层函数）
agent = Agent(model=..., tools=..., hooks=[on_before])
```

### 4.2 HookOrder 优先级

```python
from strands.hooks import HookOrder

# 预设值（数字越小越先执行）：
# SDK_FIRST          = -100
# INTERVENTION_OUTPUT = -90
# DEFAULT            = 0
# MODEL_ROUTING      = 50
# INTERVENTION_INPUT = 90
# SDK_LAST           = 100

agent.hooks.add_callback(EventType, fn1, order=HookOrder.SDK_FIRST)
agent.hooks.add_callback(EventType, fn2, order=HookOrder.DEFAULT)
agent.hooks.add_callback(EventType, fn3, order=HookOrder.SDK_LAST)
```

实测输出顺序：`SDK_FIRST → DEFAULT → SDK_LAST`。

### 4.3 EventType vs HookProvider

```python
# 方式 1：直接传函数（需 event 类型注解）
def on_x(event: EventX) -> None: ...


# 方式 2：HookProvider（适合复杂场景）
from strands.hooks import HookProvider, HookRegistry


class MyHookProvider(HookProvider):
    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(BeforeModelCallEvent, self.on_before)
        registry.add_callback(AfterModelCallEvent, self.on_after)

    def on_before(self, event): ...
    def on_after(self, event): ...


agent = Agent(model=..., hooks=[MyHookProvider()])
```

---

## 五、6 种实战场景

### 场景 1：审计日志（生产必备）

```python
import json, logging
from strands.hooks import BeforeToolCallEvent, AfterToolCallEvent

audit_logger = logging.getLogger("audit")


def audit_before(event: BeforeToolCallEvent) -> None:
    audit_logger.info(
        json.dumps(
            {
                "event": "tool_call_start",
                "tool": event.tool_use["name"],
                "input": event.tool_use.get("input"),
            },
            ensure_ascii=False,
        )
    )


def audit_after(event: AfterToolCallEvent) -> None:
    audit_logger.info(
        json.dumps(
            {
                "event": "tool_call_end",
                "tool": event.tool_use["name"],
                "duration_s": event.duration,
                "success": event.exception is None,
            },
            ensure_ascii=False,
        )
    )


agent.hooks.add_callback(BeforeToolCallEvent, audit_before)
agent.hooks.add_callback(AfterToolCallEvent, audit_after)
```

### 场景 2：工具调用白名单/黑名单

```python
from strands.hooks import BeforeToolCallEvent

BLOCKED = {"shell", "file_write", "editor"}  # 禁用危险工具


def enforce_whitelist(event: BeforeToolCallEvent) -> None:
    if event.tool_use["name"] in BLOCKED:
        event.cancel_tool = True  # 拒绝


agent.hooks.add_callback(BeforeToolCallEvent, enforce_whitelist)
```

### 场景 3：参数注入

```python
from strands.hooks import BeforeToolCallEvent


# 自动给所有 calculator 调用加 precision=2
def inject_precision(event: BeforeToolCallEvent) -> None:
    if event.tool_use["name"] == "calculator":
        event.tool_use["input"].setdefault("precision", 2)


agent.hooks.add_callback(BeforeToolCallEvent, inject_precision)
```

### 场景 4：token 用量限流

```python
from strands.hooks import BeforeModelCallEvent

MAX_TOKENS_PER_TURN = 8000


def limit_tokens(event: BeforeModelCallEvent) -> None:
    if event.projected_input_tokens and event.projected_input_tokens > MAX_TOKENS_PER_TURN:
        event.cancel = True  # 取消，让上层决定怎么压缩上下文


agent.hooks.add_callback(BeforeModelCallEvent, limit_tokens)
```

### 场景 5：成本追踪

```python
from strands.hooks import AfterModelCallEvent

total_cost = 0.0
COST_PER_1K = {"input": 0.003, "output": 0.015}  # Claude Sonnet


def track_cost(event: AfterModelCallEvent) -> None:
    global total_cost
    if event.stop_response:
        usage = event.stop_response.usage  # Usage(inputTokens, outputTokens)
        cost = (
            usage.inputTokens * COST_PER_1K["input"] + usage.outputTokens * COST_PER_1K["output"]
        ) / 1000
        total_cost += cost


agent.hooks.add_callback(AfterModelCallEvent, track_cost)
```

### 场景 6：用户中断检查

```python
from strands.hooks import BeforeInvocationEvent

user_cancelled = threading.Event()


def check_cancel(event: BeforeInvocationEvent) -> None:
    if user_cancelled.is_set():
        raise RuntimeError("用户取消")


agent.hooks.add_callback(BeforeInvocationEvent, check_cancel)
```

---

## 六、踩坑记录

### 🔴 坑 1：嵌套函数 + 类型注解 = 注册失败

详见 Step 1 文档 §四.坑 1。
**解决**：把 callback 放到模块顶层，或用 `agent.hooks.add_callback(EventType, fn)` 显式注册。

### 🔴 坑 2：BeforeToolCallEvent.cancel_tool 不等于抛异常

```python
# ❌ 错误：抛异常 = 整个 agent() 调用失败
def validate(event: BeforeToolCallEvent) -> None:
    if bad:
        raise ValueError("拒绝")  # 用户看到的是 Agent error


# ✅ 正确：用 cancel_tool 让工具"返回错误结果"，agent 继续
def validate(event: BeforeToolCallEvent) -> None:
    if bad:
        event.cancel_tool = True  # Agent 收到 tool_result status="error"
```

### 🔴 坑 3：BeforeModelCallEvent.cancel 触发的是 end_turn

```python
event.cancel = True
# → 模型直接返回 end_turn，不再调 LLM
# → result.message 是预设的"cancelled by hook"占位文本
```

如果想让 agent 整体停止，应该 raise 而不是 cancel。

### 🟡 坑 4：修改 event.tool_use["input"] 真的会影响工具调用

实测：BeforeToolCallEvent 里修改 `input` 字段，工具拿到的就是修改后的 input。

### 🟡 坑 5：MessageAddedEvent 触发很频繁

每次 messages list 追加都会触发，包括 `tool_use`、`tool_result`、assistant 文本。频繁的 hook 可能影响性能。

### 🟡 坑 6：HookOrder 数字小 = 先执行

```python
HookOrder.SDK_FIRST = -100  # 最先
HookOrder.DEFAULT = 0  # 默认
HookOrder.SDK_LAST = 100  # 最后
```

### 🟡 坑 7：AfterModelCallEvent.retry 会触发新一轮 event_loop_cycle

`retry=True` 会让整个 event_loop_cycle 重跑（不是同一 cycle 内重试），所以会看到 `cycle_count += 1`。

---

## 七、Hooks vs Middleware 选择

| 需求 | 用 Hooks | 用 Middleware |
|------|---------|--------------|
| 改 model 输入 | ✅ BeforeModelCallEvent | ❌ |
| 改 tool 输入 | ✅ BeforeToolCallEvent | ✅ ExecuteToolStage.Input |
| 改 tool 输出 | ✅ AfterToolCallEvent | ✅ ExecuteToolStage.Output |
| 改最终响应 | ❌（要在 EventLoopStopEvent）| ✅ AgentStreamStage.Output |
| 重试 | ✅ AfterModelCallEvent.retry | ✅ Wrap 阶段 |
| 复杂编排 | ❌（hooks 是扁平的）| ✅（嵌套 next_fn 链）|

---

## 八、参考

- 源码：`strands/hooks/registry.py`、`strands/hooks/events.py`
- 官方文档：<https://strandsagents.com/docs/user-guide/concepts/hooks/>
- 本地验证：`tests/demo_hooks.py --all`