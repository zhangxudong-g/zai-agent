# 🎯 Callback Handler 实战

> 配套代码：`tests/demo_callback.py`（6 种模式可一键复现）
> 验证时间：2026-08-26 / strands-agents 1.53.0
> 验证方式：`python tests/demo_callback.py --all`

---

## 一、概念区分：callback_handler vs hooks

这是新手最容易混的两个概念。

| 特性 | `callback_handler` | `hooks` |
|------|-------------------|---------|
| **用途** | 流式输出到终端 / UI | 拦截 / 修改事件 |
| **触发粒度** | 每个 chunk（高频） | 每次事件（低频） |
| **签名** | `def cb(**kwargs)` | `def cb(event: EventType)` |
| **类型** | kwargs 字典 | 强类型 event 对象 |
| **数量** | Agent 只接 1 个 | 列表，可注册多个 |
| **典型用途** | UI 流式渲染、日志显示 | 审计、安全拦截、metrics 收集 |
| **类比** | React 的 setState 回调 | Express 的 middleware |

**官方 Quickstart 用的是 `callback_handler`**（流式 UI 风格）。
**生产工程用 `hooks`**（拦截 + 类型安全 + 可组合）。

---

## 二、6 种模式速查表

| 模式 | 用途 | 关键技巧 |
|------|------|---------|
| 1. 最简 data + tool_use | 流式 UI | 识别 `kwargs` 里有没有 `data` / `current_tool_use` |
| 2. 工具调用收集器 | 审计 + 调试 | 用 `toolUseId` 去重 |
| 3. 类型注解 hooks | 强类型拦截 | ⚠️ 嵌套函数 + 类型推断有坑（见 §四） |
| 4. 多 callback 组合 | 一个 callback_handler 里跑多个 | 自己写 `composed()` |
| 5. 过滤工具结果 | 隐藏工具细节，只看文本 | 用 `current_tool_use` + 状态机 |
| 6. JSONL 全量落盘 | 审计、回放、调试 | 把所有 kwargs 序列化为 JSONL |

---

## 三、demo 运行结果（实测）

```text
[Pattern 1] 最简 data + tool_use 捕获      PASS (4.3s)
[Pattern 2] 工具调用收集器                  PASS (3.1s)
[Pattern 3] 类型注解 hooks                 PASS (2.7s)
[Pattern 4] 多 callback 组合               PASS (2.9s)
[Pattern 5] 过滤工具结果                   PASS (1.6s)
[Pattern 6] JSONL 全量落盘                 PASS (1.6s)
结果：6/6 passed
```

> ⚠️ 端点偶发 DNS 抖动（`Errno 11001 getaddrinfo failed`），单跑稳定；网络抖动请重试。

---

## 四、踩坑记录

### 🔴 坑 1：嵌套函数 + 类型注解 = `get_type_hints` 解析失败

**症状**：
```python
def outer():
    from strands.hooks import BeforeModelCallEvent  # 局部 import
    def inner(event: BeforeModelCallEvent) -> None: pass  # 引用上面
    Agent(hooks=[inner])  # ❌ ValueError: cannot infer event type
```

**原因**：`hooks=[inner]` 走 `add_hook()` → `add_callback(None, fn)` → `infer_event_types(fn)` → `get_type_hints(fn)`。
`get_type_hints` 看的是 fn 的 `__globals__`，那是**模块级 globals**，**不是**外层函数的局部 globals。所以局部 import 引入的 `BeforeModelCallEvent` 找不到。

**解决（二选一）**：

A. **把 callback 提到模块顶层**（最干净）：
```python
from strands.hooks import BeforeModelCallEvent  # 模块顶部

def on_before_model(event: BeforeModelCallEvent) -> None:
    ...

def main():
    Agent(hooks=[on_before_model])  # ✅ 工作
```

B. **构造完 Agent 后手动注册**（推荐用于动态场景）：
```python
agent = Agent(model=..., tools=...)
agent.hooks.add_callback(BeforeModelCallEvent, inner_fn)  # ✅ 工作
```

### 🟡 坑 2：callback_handler 看不懂 `event` 对象

callback_handler 的事件签名是 `**kwargs`，不是强类型 event。所以：

```python
def cb(**kwargs):
    if "data" in kwargs:           # ✅ 流式文本
        ...
    elif "current_tool_use" in kwargs:  # ✅ 工具调用 delta
        t = kwargs["current_tool_use"]
        ...
    elif "message" in kwargs:      # ✅ 最终消息
        ...
    else:
        # ⚠️ 兜底：可能是你没识别的事件类型
        print("Unknown event:", kwargs.keys())
```

如果想拿强类型事件（BeforeModelCallEvent 等），**用 hooks，不要用 callback_handler**。

### 🟡 坑 3：`callback_handler` 收到的不是 `tool_result`

回调看到的是 **`current_tool_use`**（模型决定调用工具），看不到 `tool_result`（工具返回）。
要看工具结果，要么 `agent.stream_async()` 循环里读 `tool_result` event，要么用 hooks 的 `AfterToolCallEvent`。

### 🟡 坑 4：去重要用 `toolUseId`

```python
# ❌ 错误：current_tool_use 会在每个 chunk 都触发，可能重复
def cb(**kwargs):
    if "current_tool_use" in kwargs and kwargs["current_tool_use"].get("name"):
        tool_uses.append(kwargs["current_tool_use"]["name"])

# ✅ 正确：用 toolUseId 去重（同一工具调用产生多个 delta，但 toolUseId 相同）
def cb(**kwargs):
    if "current_tool_use" in kwargs:
        t = kwargs["current_tool_use"]
        if t.get("toolUseId") and t["toolUseId"] not in seen:
            tool_uses.append(t["name"])
            seen.add(t["toolUseId"])
```

### 🟡 坑 5：多层嵌套回调的副作用

```python
def cb(**kwargs):
    print(kwargs)  # 如果 kwargs 里有大对象，会很慢
    raise Exception("oops")  # ⚠️ 抛异常会让 Agent 终止
```

callback_handler 里抛异常 = 整个 stream 终止。用 hooks 的 `BeforeModelCallEvent.cancel` 字段是更优雅的取消方式。

---

## 五、6 种模式完整示例

### Pattern 1：最简 data + tool_use 捕获

```python
from strands import Agent
from strands_tools import calculator

captured = []
tool_uses = []

def cb(**kwargs):
    if "data" in kwargs:
        captured.append(kwargs["data"])
    elif "current_tool_use" in kwargs:
        t = kwargs["current_tool_use"]
        if t.get("name") and t["name"] not in tool_uses:
            tool_uses.append(t["name"])

agent = Agent(model=model, tools=[calculator], callback_handler=cb)
agent("What is 123 * 456?")
# → captured: 50+ data chunks, tool_uses: ['calculator']
```

### Pattern 2：工具调用收集器

```python
calls = []

def cb(**kwargs):
    if "current_tool_use" in kwargs:
        t = kwargs["current_tool_use"]
        if t.get("name"):
            calls.append({
                "tool_use_id": t.get("toolUseId"),
                "name": t.get("name"),
                "input": t.get("input", {}),
            })

agent = Agent(model=model, tools=[calculator], callback_handler=cb)
agent("Calculate (99 + 1) * 25")
# → calls: [{tool_use_id, name='calculator', input={'expression': '(99+1)*25'}}]
```

### Pattern 3：类型注解 hooks（避开嵌套函数坑）

```python
from strands import Agent
from strands_tools import calculator
from strands.hooks import BeforeModelCallEvent, AfterModelCallEvent

# ⚠️ 这两个函数必须在模块顶层（或传入 add_callback 时显式指定事件类型）
def on_before(event: BeforeModelCallEvent) -> None:
    print(f"model call: projected_tokens={event.projected_input_tokens}")

def on_after(event: AfterModelCallEvent) -> None:
    print(f"model result: stop_reason={event.stop_response.stop_reason}")

agent = Agent(model=model, tools=[calculator], callback_handler=None)
agent.hooks.add_callback(BeforeModelCallEvent, on_before)
agent.hooks.add_callback(AfterModelCallEvent, on_after)

agent("What is 2+2?")
# → 控制台打印 model call 和 model result 两行
```

### Pattern 4：多 callback 组合（一个 callback_handler 里跑多个）

```python
def cb_a(**kwargs): print("A:", kwargs.get("data", ""))
def cb_b(**kwargs): print("B:", kwargs.get("data", ""))

def composed(**kwargs):
    cb_a(**kwargs)
    cb_b(**kwargs)

agent = Agent(model=model, tools=[calculator], callback_handler=composed)
# → 每次 data 事件触发两次：A: ... B: ...
```

### Pattern 5：过滤工具结果

```python
in_tool_result = False
visible = []

def cb(**kwargs):
    global in_tool_result
    if "current_tool_use" in kwargs:
        if kwargs["current_tool_use"].get("name"):
            in_tool_result = True
    elif "data" in kwargs:
        if in_tool_result:
            in_tool_result = False  # 跳过工具输出
        else:
            visible.append(kwargs["data"])

agent = Agent(model=model, tools=[calculator], callback_handler=cb)
agent("What is 7 * 8?")
# → 终端只看到模型自然语言文本，看不到 calculator 的 "Result: 56"
```

### Pattern 6：JSONL 全量落盘

```python
import json, time

log_path = "callback.jsonl"
seq = 0

def cb(**kwargs):
    global seq
    seq += 1
    if "data" in kwargs:
        kind = "data"
        payload = kwargs["data"]
    elif "current_tool_use" in kwargs:
        kind = "tool_use"
        payload = {
            "tool_use_id": kwargs["current_tool_use"].get("toolUseId"),
            "name": kwargs["current_tool_use"].get("name"),
        }
    else:
        kind = "other"
        payload = {k: str(v)[:200] for k, v in kwargs.items()}
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"seq": seq, "ts": time.time(),
                            "kind": kind, "payload": payload},
                           ensure_ascii=False) + "\n")

agent = Agent(model=model, tools=[calculator], callback_handler=cb)
agent("What is 5 * 5?")
# → callback.jsonl 包含完整事件流，可回放、可审计
```

---

## 六、何时该用哪种？

```
决策树：
├── 想要"模型一边生成一边显示"的 UI 流式体验
│   └── 用 callback_handler（Pattern 1）
├── 想要"收集所有工具调用做审计/统计"
│   └── 用 callback_handler（Pattern 2）或 hooks 的 Before/AfterToolCallEvent
├── 想要"在某些事件上拦截 / 修改 / 取消"
│   └── 一定用 hooks（Pattern 3），不要用 callback_handler
├── 想要"一个 callback 里跑多个逻辑"
│   └── 自己 compose（Pattern 4）
├── 想要"隐藏工具细节，只看模型文本"
│   └── callback_handler + 状态机（Pattern 5）
└── 想要"完整事件流可回放"
    └── JSONL 落盘（Pattern 6）
```

---

## 七、参考

- 官方 Quickstart：<https://strandsagents.com/docs/user-guide/quickstart/python/#callback-handlers>
- 源码：`strands/hooks/registry.py`、`strands/hooks/_type_inference.py`
- 本地验证：`tests/demo_callback.py --all`