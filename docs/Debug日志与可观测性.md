# 🔍 Debug 日志与可观测性实战

> 配套代码：`tests/demo_debug.py`（7 种模式可一键复现）
> 验证时间：2026-08-26 / strands-agents 1.53.0
> 验证方式：`python tests/demo_debug.py --all`

---

## 一、可观测性三大支柱

| 支柱 | Strands 对应 | 何时用 |
|------|-------------|--------|
| **Logs（日志）** | `logging.getLogger("strands")` | 开发调试、问题排查 |
| **Metrics（指标）** | `result.metrics` / `result.metrics.accumulated_usage` | 性能监控、成本核算 |
| **Traces（追踪）** | `result.metrics.traces` / `event_loop_cycle.span` | 链路追踪、慢请求分析 |

Strands 1.53.0 三者都内置了，**不需要装 OpenTelemetry 也能拿到前两层**。

---

## 二、7 种模式速查表

| 模式 | 用途 | 关键代码 |
|------|------|---------|
| 1. 基础 DEBUG | 看所有内部事件 | `getLogger("strands").setLevel(DEBUG)` |
| 2. INFO 级别 | 关键节点（少 10× 噪音） | 同上，DEBUG → INFO |
| 3. 落盘 + 轮转 | 长期保存、磁盘不爆 | `RotatingFileHandler(maxBytes, backupCount)` |
| 4. 按 logger 名过滤 | 只看 event_loop，关闭其他 | `getLogger("strands.event_loop")` |
| 5. EventLoopMetrics 探索 | 看 cycle_count / usage / latency | `result.metrics.*` |
| 6. traces 探索 | 看每轮的起止时间、父子结构 | `result.metrics.traces` |
| 7. 禁用第三方噪音 | 屏蔽 urllib3/botocore 刷屏 | 单独 setLevel(WARNING) |

---

## 三、demo 运行结果（实测）

```text
[Pattern 1] 基础 DEBUG 日志          PASS (11.4s)
[Pattern 2] INFO 级别过滤            PASS (1.8s)
[Pattern 3] 落盘 + 轮转              PASS (1.7s)
[Pattern 4] 按 logger 名过滤         PASS (1.7s)
[Pattern 5] EventLoopMetrics 探索   PASS (3.0s)
[Pattern 6] traces 探索             PASS (1.9s)
[Pattern 7] 禁用第三方噪音          PASS (2.0s)
结果：7/7 passed
```

---

## 四、核心 API 速查

### 4.1 日志层级

```
strands                                  (顶层)
├── strands.agent                       (Agent 构造)
├── strands.agent.conversation_manager
│   └── strands.agent.conversation_manager.sliding_window_conversation_manager
├── strands.event_loop                  (事件循环)
│   ├── strands.event_loop.streaming    (流处理)
│   └── strands.event_loop._retry       (重试)
├── strands.models.ollama               (模型)
├── strands.models.bedrock
├── strands.tools                       (工具)
│   ├── strands.tools.registry
│   ├── strands.tools.loader
│   └── strands.tools.executors._executor
├── strands.hooks                       (钩子)
├── strands.plugins.registry            (插件)
└── strands.telemetry.metrics           ( telemetry)
```

**实战配方**：
```python
# 开发：看 event_loop + tools 的全部
logging.getLogger("strands.event_loop").setLevel(logging.DEBUG)
logging.getLogger("strands.tools").setLevel(logging.DEBUG)

# 生产：只 WARNING
logging.getLogger("strands").setLevel(logging.WARNING)
```

### 4.2 `result.metrics` 字段（1.53.0 实测）

```python
@dataclass
class EventLoopMetrics:
    cycle_count: int  # 总循环数
    tool_metrics: dict[str, ToolMetrics]  # 每个工具的统计
    cycle_durations: list[float]  # 每轮耗时（秒）
    agent_invocations: list[AgentInvocation]  # 每次调用的详情
    traces: list[Trace]  # 链路追踪
    accumulated_usage: Usage  # 累计 token
    accumulated_metrics: Metrics  # 累计延迟
```

**实际访问模式**（⚠️ 教程和实际有差异，见 §六）：

| 字段 | 1.53.0 正确路径 |
|------|---------------|
| 输入/输出 tokens | `result.metrics.accumulated_usage.inputTokens` |
| 累计延迟 | `result.metrics.accumulated_metrics.latencyMs` |
| 工具调用次数 | `result.metrics.tool_metrics[name].call_count` |
| 循环数 | `result.metrics.cycle_count` |
| 每轮耗时 | `result.metrics.cycle_durations` (list[float]) |
| 链路追踪 | `result.metrics.traces` (list[Trace]) |
| 每次调用 | `result.metrics.agent_invocations` (list) |

### 4.3 Trace 结构

```python
Trace(
    name="Cycle 1",
    id="474c42ff-8dcd-4640-aede-afcf31244800",
    parent_id=None,
    start_time=1787706492.67,
    end_time=1787706494.93,
    messages=[],
    children=[Trace(...)],  # 嵌套子 trace（如 model invoke span）
)
```

---

## 五、踩坑记录

### 🔴 坑 1：Ollama 不返回标准 token 计数

**症状**：
```json
"accumulated_usage": {
  "inputTokens": null,
  "outputTokens": null,
  "totalTokens": null
}
```

**原因**：Ollama API 不返回标准 usage 字段（不像 Bedrock/Anthropic）。

**解决**：
- 看 `cycle_durations`（用时间做粗略指标）
- 在 `BeforeModelCallEvent.projected_input_tokens` 拿估算
- 用其他模型（Anthropic / Bedrock）

### 🟡 坑 2：DEBUG 日志只在 `strands` logger 上

```python
# ❌ 错误：无效
logging.getLogger().setLevel(logging.DEBUG)

# ✅ 正确
logging.getLogger("strands").setLevel(logging.DEBUG)
```

### 🟡 坑 3：第三方库刷屏

`urllib3`、`botocore`、`httpx`、`httpcore` 在 DEBUG 时会输出大量 HTTP 请求细节。

**解决**：
```python
for noisy in ("urllib3", "botocore", "boto3", "httpx", "httpcore"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
```

### 🟡 坑 4：`RotatingFileHandler` 要 close

```python
handler = RotatingFileHandler(...)
# ... 使用 ...
handler.flush()
handler.close()  # ⚠️ 不关可能丢日志
```

### 🟡 坑 5：Trace 的 messages 字段是空的

实测 `Trace.messages` 永远是 `[]`，因为 Strands 的 Trace 主要记录**结构（start/end/parent/children）**而不是消息内容。
要看消息内容，用 `result.metrics.agent_invocations[i].cycles[j].usage`。

---

## 六、与官方文档的差异

官方 Quickstart 提到 `result.metrics.get_summary()`，实际拿到的 summary 结构：

```json
{
  "cycle_count": 1,
  "tool_metrics": {"calculator": {"call_count": 1, "success_count": 1, ...}},
  "cycle_durations": [2.34],
  "agent_invocations": [{"usage": {...}, "cycles": [...]}],
  "accumulated_usage": {"inputTokens": null, ...},   ← Ollama 不填
  "accumulated_metrics": {"latencyMs": null}          ← Ollama 不填
}
```

注意：**官方 Quickstart 暗示这些字段在 `result.*` 直接持有，实际都在 `result.metrics.*`**。

---

## 七、最佳实践

### 7.1 开发环境

```python
import logging

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s | %(name)s | %(message)s",
)
# 按需打开
logging.getLogger("strands.event_loop").setLevel(logging.DEBUG)
logging.getLogger("strands.tools").setLevel(logging.INFO)
```

### 7.2 生产环境

```python
# 1. 关闭所有 DEBUG
logging.getLogger("strands").setLevel(logging.WARNING)

# 2. 落 INFO 级别到文件（审计用）
from logging.handlers import RotatingFileHandler

audit_handler = RotatingFileHandler(
    "/var/log/strands/audit.log", maxBytes=100 * 1024 * 1024, backupCount=10, encoding="utf-8"
)
audit_handler.setLevel(logging.INFO)
logging.getLogger("strands").addHandler(audit_handler)

# 3. 关键 hook 做结构化日志
from strands.hooks import BeforeToolCallEvent


def audit_tool(event: BeforeToolCallEvent) -> None:
    logger.info(
        json.dumps(
            {
                "event": "tool_call",
                "name": event.tool_use["name"],
                "input": event.tool_use.get("input"),
            }
        )
    )


agent.hooks.add_callback(BeforeToolCallEvent, audit_tool)
```

### 7.3 接入 OpenTelemetry（可选）

Strands 1.53.0 已经预装 `opentelemetry-api`，可以直接用：

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider()
trace.set_tracer_provider(provider)

# Strands 会自动用全局 tracer，无需额外配置
```

更深入的 OTLP exporter 配置需要装 `opentelemetry-exporter-otlp`。

---

## 八、参考

- 官方 Quickstart：<https://strandsagents.com/docs/user-guide/quickstart/python/#debug-logs>
- 源码：`strands/telemetry/metrics.py`、`strands/telemetry/tracer.py`
- 本地验证：`tests/demo_debug.py --all`