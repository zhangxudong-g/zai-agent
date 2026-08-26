# 📦 AgentResult 字段详解实战

> 配套代码：`tests/demo_agent_result.py`（7 种模式可一键复现）
> 验证时间：2026-08-26 / strands-agents 1.53.0
> 验证方式：`python tests/demo_agent_result.py --all`

---

## 一、AgentResult 是什么

每次 `agent(prompt)` 调用结束后，Strands 返回一个 **`AgentResult`** 数据类实例：

```python
@dataclass
class AgentResult:
    stop_reason: StopReason            # 停止原因枚举
    message: Message                   # 最后一条 assistant 消息
    metrics: EventLoopMetrics         # 性能/用量指标（详见 Step 2）
    state: Any                        # 事件循环累积状态
    interrupts: Sequence[Interrupt] | None = None
    structured_output: BaseModel | None = None
    checkpoint: Checkpoint | None = None
```

它**不是 dict**，是 dataclass；不是普通的 dict-state，是 JSONSerializableDict。

---

## 二、7 个字段速查表

| 字段 | 类型 | 一句话 | 何时用 |
|------|------|--------|--------|
| `stop_reason` | `StopReason` 字面量 | "为什么停" | 判断要不要继续 |
| `message` | `dict`（Message）| 最后一条消息 | 拿最终文本/工具调用 |
| `metrics` | `EventLoopMetrics` | 性能+用量 | 监控/计费 |
| `state` | `Any`（实际是 dict）| 跨调用持久状态 | 跨轮保存数据 |
| `interrupts` | `Sequence[Interrupt]`\|None | 中断列表 | 处理需要决策的事件 |
| `structured_output` | `BaseModel`\|None | 结构化输出 | Pydantic 集成 |
| `checkpoint` | `Checkpoint`\|None | 持久化快照 | 长任务恢复 |

外加 2 个 property：
- `result.context_size`：最近一次 LLM 输入 token 数
- `result.projected_context_size`：预计下一轮 inputTokens + outputTokens

---

## 三、demo 运行结果（实测）

```text
[Pattern 1] 顶层结构              PASS (3.0s)
[Pattern 2] message blocks         PASS (2.3s)
[Pattern 3] stop_reason 取值       PASS (4.1s)
[Pattern 4] state 字段             PASS (11.8s)
[Pattern 5] message 序列化         PASS (1.7s)
[Pattern 6] structured_output      PASS (4.7s)
[Pattern 7] context_size property  PASS (1.5s)
结果：7/7 passed
```

---

## 四、字段详解

### 4.1 `stop_reason`

```python
from strands.types.streaming import StopReason
# StopReason = Literal[
#     "end_turn",       ← 正常结束（最常见）
#     "tool_use",       ← 工具调用后还会继续（一般不在 result 里看到）
#     "max_tokens",     ← 输出达到上限被截断
#     "limit_turns",    ← 超过配置的 turn 上限
#     "cancelled",      ← 用户取消
#     "checkpoint",      ← 进入持久化检查点模式
#     "interrupt",      ← 中断（需要用户决策）
#     "error",          ← 出错
# ]
```

**判断"还要继续吗"**：
```python
if result.stop_reason == "end_turn":
    print("正常完成")
elif result.stop_reason == "max_tokens":
    print("被截断，可能需要更长 max_tokens")
elif result.stop_reason == "interrupt":
    handle_interrupts(result.interrupts)
```

### 4.2 `message`

```python
msg = result.message
# msg = {
#   "role": "assistant",
#   "content": [
#     {"text": "The answer is 144."},
#     # 也可能包含：
#     # {"toolUse": {"toolUseId": "...", "name": "...", "input": {...}}},
#     # {"toolResult": {"toolUseId": "...", "status": "...", "content": [...]}},
#     # {"citationsContent": {...}},
#   ]
# }
```

**拿最终文本**（3 种写法）：
```python
text1 = msg["content"][0]["text"]                    # 直接拿
text2 = str(result)                                    # AgentResult.__str__ 会自动提取文本
text3 = "".join(b["text"] for b in msg["content"]
                if "text" in b)                       # 拼接所有文本块
```

### 4.3 `metrics`（详见 Step 2 文档）

```python
result.metrics.cycle_count
result.metrics.accumulated_usage
result.metrics.cycle_durations
result.metrics.traces
result.metrics.tool_metrics
result.metrics.accumulated_metrics
```

### 4.4 `state` —— 跨调用的持久状态

**两种"state"概念，容易混**：

| 名字 | 类型 | 生命周期 | 用途 |
|------|------|---------|------|
| `agent.state` | `JSONSerializableDict` | Agent 整个生命周期 | 跨轮保存数据 |
| `result.state` | `dict`（快照）| 单次调用结束 | 拿这次调用的状态 |

**agent.state 写入**（用 `.set()`）：
```python
from strands.hooks import BeforeModelCallEvent

def inject(event: BeforeModelCallEvent) -> None:
    event.agent.state.set("user_id", "user-42")     # ✅
    event.agent.state.set("count", 3)               # ✅ int
    event.agent.state.set("tags", ["a", "b"])       # ✅ list

agent.hooks.add_callback(BeforeModelCallEvent, inject)
```

**⚠️ 禁用 `state["key"] = value`**（TypeError: 'JSONSerializableDict' object does not support item assignment）

**读取**：
```python
agent.state.get("user_id")           # 拿单个 key
agent.state.get()                     # 拿整个 dict 副本
```

### 4.5 `interrupts`

当中断机制触发时（详见 Hooks 章节），`interrupts` 不为空：

```python
result.interrupts
# [
#   Interrupt(
#       id="interrupt-001",
#       name="dangerous_operation",
#       reason="即将删除文件",
#       response=None,  # 等用户填
#   ),
# ]
```

### 4.6 `structured_output`（Pydantic 集成）

**用法**：
```python
from pydantic import BaseModel
from strands import Agent

class MathResult(BaseModel):
    question: str
    answer: int

agent = Agent(model=model, tools=[...])
result = agent(
    "Calculate 100 * 25 and return the answer",
    structured_output_model=MathResult,
)

print(result.structured_output)        # MathResult(question=..., answer=2500)
print(result.structured_output.model_dump_json())
```

**注意**：
- Ollama + 某些模型可能不支持 `tool_choice` 强制结构化，会抛异常
- Anthropic / Bedrock 通常支持

### 4.7 `checkpoint`（实验性）

`Agent(checkpointing=True)` 开启后，长任务可以暂停-恢复：

```python
result.checkpoint
# Checkpoint(
#     cycle_index=3,
#     position="after_tools",
#     messages=[...],
#     ...
# )
```

⚠️ 1.53.0 是 experimental，可能 API 还会变。

---

## 五、踩坑记录

### 🔴 坑 1：state 不是 dict，不能用 `state["k"] = v`

```python
# ❌ 报错
agent.state["user_id"] = "u-1"
# TypeError: 'JSONSerializableDict' object does not support item assignment

# ✅ 用 .set()
agent.state.set("user_id", "u-1")
```

### 🔴 坑 2：`agent.state` vs `result.state` 不是同一个

- `agent.state`：JSONSerializableDict，**跨调用持久**
- `result.state`：dict 快照，**单次调用结束后的副本**

修改 agent.state 不会反映在 result.state 里（因为 result 是 agent 调用前 state 的浅拷贝）。

### 🟡 坑 3：`result.metrics.accumulated_usage` Ollama 永远是 None

详见 Step 2 文档 §五.坑 1。

### 🟡 坑 4：`structured_output_model` 强制模型调用工具

很多模型对 `structured_output_model` 的实现是"循环调用直到输出符合 schema"，所以一次问询可能触发多次 LLM 调用。设 `max_tokens` 和 token 上限很重要。

### 🟡 坑 5：`AgentResult` 不是 Pydantic 模型

不能用 `.model_dump()`。要看 JSON：
```python
import json
result_dict = {
    "stop_reason": result.stop_reason,
    "message": result.message,
    # metrics 需要手动拆
}
json.dumps(result_dict, default=str, ensure_ascii=False)
```

---

## 六、完整示例：处理一个 tool_use 回合的 result

```python
from strands import Agent
from strands_tools import calculator

agent = Agent(model=model, tools=[calculator], callback_handler=None)
result = agent("Calculate 7 * 8 and explain the result")

# 1. 检查停止原因
print(f"stop_reason: {result.stop_reason}")
assert result.stop_reason == "end_turn"

# 2. 拿最终文本
final_text = str(result)
print(f"Model said: {final_text}")

# 3. 看是否调用了工具
if "calculator" in result.metrics.tool_metrics:
    calc_stats = result.metrics.tool_metrics["calculator"]
    print(f"Calculator called {calc_stats.call_count}x")

# 4. 看耗时
print(f"Total duration: {result.metrics.cycle_durations}")
print(f"Total cycles: {result.metrics.cycle_count}")

# 5. 如果有 interrupts，处理
if result.interrupts:
    for intr in result.interrupts:
        print(f"Need decision: {intr.name} - {intr.reason}")
```

---

## 七、参考

- 源码：`strands/agent/agent_result.py`、`strands/types/json_dict.py`
- Step 2 文档：`docs/Debug日志与可观测性.md`（metrics 详解）
- 本地验证：`tests/demo_agent_result.py --all`