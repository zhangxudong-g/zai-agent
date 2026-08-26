# 🧅 Middleware 体系实战

> 配套代码：`tests/demo_middleware.py`（6 种模式可一键复现）
> 验证时间：2026-08-26 / strands-agents 1.53.0
> 验证方式：`python tests/demo_middleware.py --all`

---

## 一、Middleware 是什么

Strands 1.53.0 的 Middleware 是**洋葱模型**（onion model）：

```
外层 middleware  进入 →
   中层 middleware  进入 →
      内层 middleware  进入 →
         [实际调用 LLM / 工具]
      ← 内层 middleware  退出
   ← 中层 middleware  退出
外层 middleware  退出
```

像 Express/Koa 的 middleware，可以多层嵌套、拦截、修改。

---

## 二、3 个 Stage × 3 个 Phase

| Stage | 拦截什么 | 典型用途 |
|-------|---------|---------|
| `InvokeModelStage` | LLM 调用 | 修改 messages、加 metadata、计时 |
| `ExecuteToolStage` | 工具执行 | 工具包装、参数注入、结果审计 |
| `AgentStreamStage` | 整个输出流 | 流过滤、HITL 中断、事件转换 |

每个 Stage 有 3 个 Phase：

| Phase | 签名 | 何时执行 |
|-------|------|---------|
| `Stage.Input` | `def handler(ctx) -> ctx` | 在 stage 执行**前** transform context |
| `Stage.Wrap` | `async def handler(ctx, next_fn) -> AsyncGenerator` | 包裹整个 stage（最灵活） |
| `Stage.Output` | `def handler(result: MiddlewareResult) -> MiddlewareResult` | 在 stage 执行**后** transform result |

---

## 三、Hooks vs Middleware

| 维度 | Hooks | Middleware |
|------|-------|-----------|
| 模型 | **扁平**（每事件类型多个回调）| **洋葱**（嵌套链） |
| 改 context | 部分支持 | ✅ Wrap/Input 阶段完整改 |
| 改 result | 部分支持 | ✅ Output 阶段可改最后 event |
| 重试 | ✅ retry 字段 | ✅ Wrap 阶段可多次 yield |
| 复杂度 | 低 | 中（AOP 风格） |
| 适用场景 | 90% 需求 | 复杂编排（多步转换、HITL）|

---

## 四、demo 运行结果（实测）

```text
[Pattern 1] Wrap phase 计时           PASS (11.0s)
[Pattern 2] Input phase 注入          PASS (1.7s)
[Pattern 3] Output phase 修改         PASS (1.6s)
[Pattern 4] 多个 middleware 链       PASS (2.8s)
[Pattern 5] ExecuteToolStage          PASS (2.8s)
[Pattern 6] AgentStreamStage          PASS (1.8s)
结果：6/6 passed
```

Pattern 4 实测的洋葱执行顺序：

```
OUTER: enter → MIDDLE: enter → INNER: enter → [LLM call] →
INNER: exit → MIDDLE: exit → OUTER: exit
```

---

## 五、6 种实战场景

### 场景 1：Wrap phase 计时

```python
from strands._middleware.stages import InvokeModelStage

async def timing_mw(ctx, next_fn):
    start = time.time()
    async for event in next_fn(ctx):
        yield event
    print(f"LLM 调用耗时: {time.time() - start:.2f}s")

agent._middleware_registry.add_middleware(InvokeModelStage, timing_mw)
```

### 场景 2：Input phase 注入 context

```python
from strands._middleware.stages import InvokeModelStage

def inject_date(ctx,):
    ctx.messages.insert(0, {
        "role": "user",
        "content": [{"text": "[HINT] Today's date is 2026-08-26"}],
    })
    return ctx  # 必须返回 ctx

agent._middleware_registry.add_middleware(InvokeModelStage.Input, inject_date)
```

### 场景 3：Output phase 修改结果

```python
from strands._middleware.stages import InvokeModelStage

def add_audit_marker(result):
    if result.value and hasattr(result.value, "message"):
        content = result.value.message.get("content", [])
        content.insert(0, {"text": "[AUDITED]\n"})
    return result.replace(value=result.value)

agent._middleware_registry.add_middleware(InvokeModelStage.Output, add_audit_marker)
```

### 场景 4：多个 middleware 链式

```python
async def outer(ctx, next_fn):
    print("OUTER: enter")
    async for event in next_fn(ctx):
        yield event
    print("OUTER: exit")

async def inner(ctx, next_fn):
    print("INNER: enter")
    async for event in next_fn(ctx):
        yield event
    print("INNER: exit")

# 注册顺序：后注册的最外层
agent._middleware_registry.add_middleware(InvokeModelStage, outer)
agent._middleware_registry.add_middleware(InvokeModelStage, inner)
# 调用顺序：OUTER 进 → INNER 进 → 调用 → INNER 出 → OUTER 出
```

### 场景 5：ExecuteToolStage 工具包装

```python
from strands._middleware.stages import ExecuteToolStage

async def tool_audit(ctx, next_fn):
    tool_name = ctx.tool_use.get("name")
    print(f"[AUDIT] 调用工具: {tool_name}")
    async for event in next_fn(ctx):
        yield event
    print(f"[AUDIT] 工具 {tool_name} 完成")

agent._middleware_registry.add_middleware(ExecuteToolStage, tool_audit)
```

### 场景 6：AgentStreamStage 流过滤

```python
from strands._middleware.stages import AgentStreamStage

async def silent_mode(ctx, next_fn):
    """吞掉所有 data chunk，只保留 tool_use 等结构事件。"""
    async for event in next_fn(ctx):
        if "data" in event:
            continue  # 丢弃流式文本
        yield event  # 其他事件透传

agent._middleware_registry.add_middleware(AgentStreamStage, silent_mode)
```

---

## 六、Context 对象字段

### 6.1 `InvokeModelContext`

```python
@dataclass
class InvokeModelContext:
    agent: Agent
    messages: Messages                  # 深拷贝，可改
    system_prompt: SystemPrompt         # 深拷贝
    tool_specs: list[ToolSpec]          # 深拷贝
    tool_choice: ToolChoice | None      # 深拷贝
    invocation_state: dict[str, Any]    # 引用共享
    model: Model                        # 引用共享，可换
    projected_input_tokens: int | None
    dynamic_trailing_blocks: int = 0
```

### 6.2 `ExecuteToolContext`

```python
@dataclass
class ExecuteToolContext:
    agent: Agent | BidiAgent
    tool: AgentTool | None
    tool_use: ToolUse                   # 浅拷贝，可改 input
    invocation_state: dict[str, Any]    # 引用共享
    _interrupt_state: _InterruptState   # 内部状态

    def interrupt(self, name, *, reason=None, response=None):
        """HITL 中断"""
```

### 6.3 `AgentStreamContext`

```python
@dataclass
class AgentStreamContext:
    agent: Agent
    messages: Messages                  # 引用共享
    invocation_state: dict[str, Any]
    _interrupts: Mapping[str, Interrupt]

    def interrupt(self, name, *, reason=None, response=None):
        """HITL 中断"""
```

---

## 七、踩坑记录

### 🔴 坑 1：Input phase handler 必须返回 ctx

```python
# ❌ 忘记 return，ctx 不会传给底层
def inject(ctx): pass

# ✅ 必须返回
def inject(ctx,):
    ctx.messages.insert(0, ...)
    return ctx
```

### 🔴 坑 2：Wrap phase 必须 async generator + `async for ... yield`

```python
# ❌ 普通函数不行
def my_mw(ctx, next_fn):
    next_fn(ctx)

# ✅ 必须 async generator
async def my_mw(ctx, next_fn):
    async for event in next_fn(ctx):
        yield event
```

### 🔴 坑 3：messages 等字段是**深拷贝**，但 invocation_state 是**引用**

修改 `ctx.messages` 不会污染 agent.messages（因为是 deepcopy）。
但 `ctx.invocation_state` 改了就是真改了 agent state。

### 🟡 坑 4：Output phase handler 拿到的不是 event，是 MiddlewareResult

```python
def output(result):  # result.value 是真正的事件
    new_event = transform(result.value)
    return result.replace(value=new_event)
```

### 🟡 坑 5：注册顺序决定嵌套

实测：先注册的在内层，后注册的在**外层**（最外最先 enter，最后 exit）。

```python
agent._middleware_registry.add_middleware(InvokeModelStage, A)  # 内层
agent._middleware_registry.add_middleware(InvokeModelStage, B)  # 外层
# 调用：B enter → A enter → 调用 → A exit → B exit
```

### 🟡 坑 6：AgentStreamStage 不能改 agent.messages，只能过滤事件

ctx.messages 是引用共享，但目的是流层 context；要修改 agent 的 messages 用 BeforeModelCallEvent hook 或 agent.state。

### 🟡 坑 7：Middleware 异常会终止整个 agent() 调用

不像 hooks 用 cancel 字段是软中断，middleware 里抛异常 = 整个 agent() 调用失败。

---

## 八、何时用 Middleware 而非 Hooks？

```
决策树：
├── 只想改一个事件类型的某字段
│   └── 用 Hooks（BeforeModelCallEvent 等）
├── 想在调 LLM 前 transform 整个 messages
│   └── 用 Middleware (InvokeModelStage.Input)
├── 想在 LLM 调用前后做计时 / 日志 / 重试
│   └── 用 Middleware (InvokeModelStage.Wrap)
├── 想改 LLM 返回的最后一个 event
│   └── 用 Middleware (InvokeModelStage.Output)
├── 想拦截/转换整个 agent() 输出流
│   └── 用 Middleware (AgentStreamStage)
├── 想做工具层审计
│   └── 用 Hooks (BeforeToolCallEvent) — 更轻量
└── 想做复杂多步处理（如 HITL 审批链）
    └── 用 Middleware — 嵌套链更清晰
```

---

## 九、参考

- 源码：
  - `strands/_middleware/__init__.py`
  - `strands/_middleware/stages.py`
  - `strands/_middleware/registry.py`
- 官方文档：<https://strandsagents.com/docs/user-guide/concepts/middleware/>
- 本地验证：`tests/demo_middleware.py --all`