# 🤝 Multi-Agent 体系实战

> 配套代码：`tests/demo_multi_agent.py`（6 种模式可一键复现）
> 验证时间：2026-08-26 / strands-agents 1.53.0
> 验证方式：`python tests/demo_multi_agent.py --all`

---

## 一、Strands 1.53.0 的 4 种多 Agent 模式

| 模式 | 适合场景 | 控制粒度 |
|------|---------|---------|
| **`Agent.as_tool()`** | 简单调用，一个子 Agent 当一个工具 | 最细 |
| **`Swarm`** | 自组织 handoff，多个 agent 协作 | 中 |
| **`Graph`** | 显式 DAG，节点 + 边 + 条件 | 最显式 |
| **`A2A`**（Agent2Agent 协议）| 跨进程 / 跨语言 agent 通信 | 网络级 |

**经验法则**：
- 90% 简单需求 → `Agent.as_tool()`
- 需要 handoff/共享上下文 → `Swarm`
- 需要显式控制流（条件、并行）→ `Graph`
- 跨服务 / 跨语言 → `A2A`

---

## 二、6 种模式速查

| 模式 | 用途 | 关键 API |
|------|------|---------|
| 1. Swarm 基础 | 2 agents handoff | `Swarm([a, b], entry_point=a, max_handoffs=3)` |
| 2. Swarm 共享 | 3 agents 通过 shared_context 协作 | `Swarm(..., max_handoffs=5)` |
| 3. Graph 顺序 | A → B → C | `GraphBuilder().add_node().add_edge().set_entry_point().build()` |
| 4. Graph 条件 | 根据输出分支 | `.add_edge(from, to, condition=fn)` |
| 5. Agent.as_tool | 子 Agent 当工具 | `child.as_tool()` |
| 6. Result 探索 | SwarmResult / GraphResult 字段 | `result.status`, `result.node_history` 等 |

---

## 三、demo 运行结果（实测）

```text
[Pattern 1] Swarm 基础                PASS (3.3s)
[Pattern 2] Swarm 共享上下文          PASS (11.2s)
[Pattern 3] Graph 顺序管道            PASS (7.3s)
[Pattern 4] Graph 条件分支            PASS (2.9s)
[Pattern 5] Agent.as_tool             PASS (7.0s)
[Pattern 6] 结果对象探索              PASS (4.3s)
结果：6/6 passed
```

---

## 四、4 种模式的详细 API

### 4.1 `Agent.as_tool()` — 最轻量

```python
from strands import Agent

translator = Agent(model=..., system_prompt="你是翻译器", name="translator")
main_agent = Agent(model=..., tools=[calculator, translator.as_tool()])

# 主 Agent 调用 translator 时就像调用普通工具
result = main_agent("把'Hello'翻成中文")
```

**特点**：
- 主 Agent 完全控制何时调用子 Agent
- 子 Agent 是一次性调用（不参与多轮）
- 没有 handoff 机制

### 4.2 `Swarm` — 自组织 handoff

```python
from strands.multiagent import Swarm

researcher = Agent(name="researcher", ...)
reviewer = Agent(name="reviewer", ...)

swarm = Swarm(
    [researcher, reviewer],
    entry_point=researcher,    # 谁先开始
    max_handoffs=20,           # 最多转手次数
    max_iterations=20,
    execution_timeout=900.0,
    node_timeout=300.0,
)

result = swarm("研究问题")  # 自动决定谁来答、谁接力
```

**关键字段**（`SwarmResult`）：
```python
@dataclass
class SwarmResult(MultiAgentResult):
    node_history: list[SwarmNode]  # 执行顺序
    # 继承自 MultiAgentResult：
    status: Status  # COMPLETED / FAILED / INTERRUPTED
    results: dict[str, NodeResult]  # 每个 agent 的输出
```

### 4.3 `Graph` — 显式 DAG

```python
from strands.multiagent import GraphBuilder

# ⚠️ 注意：add_node 返回 GraphNode，不是 builder
# 所以必须分步，不能链式调用
builder = GraphBuilder()
builder.add_node(classifier, "classify")
builder.add_node(math_agent, "math")
builder.add_node(general_agent, "general")


def is_math(state):
    return "MATH" in str(list(state.results.values())[-1]).upper()


builder.add_edge("classify", "math", condition=is_math)
builder.add_edge("classify", "general", condition=lambda s: not is_math(s))
builder.set_entry_point("classify")

graph = builder.build()
result = graph("What is 5 * 5?")
```

**关键字段**（`GraphResult`）：
```python
@dataclass
class GraphResult(MultiAgentResult):
    total_nodes: int
    completed_nodes: int
    failed_nodes: int
    interrupted_nodes: int
    execution_order: list[GraphNode]
    edges: list[tuple[GraphNode, GraphNode]]
    entry_points: list[GraphNode]
```

### 4.4 `A2A`（Agent-to-Agent 协议）

```python
# 跨进程通信：用 strands.multiagent.a2a.server
from strands.multiagent.a2a import AgentServer

server = AgentServer(agent=my_agent, port=8080)
server.serve()
```

---

## 五、共享上下文 vs 独立上下文

| 模式 | shared_context | 何时用 |
|------|----------------|--------|
| `Agent.as_tool()` | ❌（主 Agent 自己管）| 简单委托 |
| `Swarm` | ✅ 自动 | 协作需要看到彼此输出 |
| `Graph` | ✅ 自动 | 条件判断要看到前置结果 |
| `A2A` | ❌（跨进程，靠消息）| 分布式 |

`state.results` 在 GraphBuilder condition 里特别有用：
```python
def my_condition(state):
    last = list(state.results.values())[-1]
    return "yes" in str(last).lower()
```

---

## 六、4 种模式选择决策树

```
我想要...
│
├── 一个 Agent 完成所有事情
│   └── 直接用 Agent
│
├── 主 Agent 在某些情况下调用子 Agent
│   └── Agent.as_tool()（最简单）
│
├── 多个 Agent 互相 handoff + 共享上下文
│   ├── 协作模式不固定
│   │   └── Swarm
│   └── 协作模式固定（A→B→C 或带条件）
│       └── Graph
│
└── 跨进程 / 跨语言调用
    └── A2A
```

---

## 七、踩坑记录

### 🔴 坑 1：`GraphBuilder.add_node()` 返回 `GraphNode`，不是 builder

```python
# ❌ 报错：GraphNode 没有 add_edge
graph = GraphBuilder().add_node(a, "x").add_edge("x", "y").build()
# AttributeError: 'GraphNode' object has no attribute 'add_edge'

# ✅ 必须分步
builder = GraphBuilder()
builder.add_node(a, "x")
builder.add_edge("x", "y")
builder.set_entry_point("x")
graph = builder.build()
```

### 🔴 坑 2：Windows GBK 终端不能打印 emoji

```python
# ❌ 如果 system_prompt 或回复含 📋 等 emoji，Windows GBK 会崩溃
agent = Agent(system_prompt="你是规划师 [EMOJI CLIPBOARD]")  # ❌

# ✅ 在 prompt 里明确说"不要用 emoji"
agent = Agent(system_prompt="你是规划师。不要用 emoji。")  # ✅
```

### 🔴 坑 3：`Swarm` 自动 handoff，但可能死循环

```python
# ❌ 没设 max_handoffs 可能无限循环
swarm = Swarm([a, b])

# ✅ 必设上限
swarm = Swarm([a, b], max_handoffs=5, max_iterations=10)
```

### 🔴 坑 4：`Graph` 的 `condition` 函数签名

```python
# ❌ 旧版：condition(state) → bool
# ✅ 新版：EdgeConditionWithContext(state, invocation_state) → bool

# 兼容两种写法：
def my_cond(state):
    return some_bool
```

### 🟡 坑 5：`SwarmResult.node_history` 里是 `SwarmNode` 对象

不是字符串：
```python
for node in result.node_history:
    print(node.node_id)  # ✅ 拿名字
    print(node.executor)  # ✅ 拿 Agent 实例
```

### 🟡 坑 6：`as_tool()` 出来的工具名

默认是子 Agent 的 `name` 属性。如果两个 Agent 同名会冲突。

### 🟡 坑 7：`Graph` 不能直接看条件边走的哪条

要 trace `_graph_state.edges_evaluated`，或者用 hook。

---

## 八、生产最佳实践

### 8.1 命名清晰

```python
Agent = Agent(
    name="researcher",  # ⚠️ Swarm 用 name 区分 Agent
    agent_id="r-001",  # 用于审计
    description="...",  # 让 as_tool 出来的工具描述更清楚
)
```

### 8.2 控制 handoff / 迭代上限

```python
swarm = Swarm(
    agents,
    max_handoffs=10,
    max_iterations=20,
    execution_timeout=600.0,  # 总超时 10 分钟
    node_timeout=60.0,  # 单个 Agent 1 分钟
)
```

### 8.3 失败兜底

```python
result = swarm(task)
if result.status == Status.FAILED:
    # 拿最后一个节点的错误
    last_node = result.node_history[-1]
    # 重试 / 降级 / 报警
```

### 8.4 配合 Hook 做审计

```python
from strands.hooks import BeforeInvocationEvent


def audit_invocation(event: BeforeInvocationEvent) -> None:
    logger.info(f"Multi-agent invocation: {event.agent.name}")


# Swarm 也有 hooks
swarm.hooks.add_callback(BeforeInvocationEvent, audit_invocation)
```

---

## 九、参考

- 源码：
  - `strands/multiagent/__init__.py`
  - `strands/multiagent/swarm.py`
  - `strands/multiagent/graph.py`
  - `strands/multiagent/a2a/`
- 官方文档：<https://strandsagents.com/docs/user-guide/concepts/multi-agent/>
- 本地验证：`tests/demo_multi_agent.py --all`