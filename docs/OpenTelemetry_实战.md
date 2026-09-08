# 📡 OpenTelemetry 实战（生产化 3.1）

> 配套代码：`tests/demo_otel.py`（6 patterns，`--all` 6/6 通过）
> 环境：strands-agents 1.53.x + opentelemetry-sdk 1.44.0 + opentelemetry-exporter-otlp-proto-http
> 模型：Ollama `qwen3.8:27b`

## 一、Strands 的 OTel 能力地图

```
strands/telemetry/
├── config.py        # StrandsTelemetry：TracerProvider 工厂 + exporter 装配
├── tracer.py        # Tracer：span 命名/属性/脱敏（REDACTED_VALUE）
├── metrics.py       # EventLoopMetrics（invocations→cycles→usage 三层）
└── metrics_constants.py
```

Span 命名约定（实测）：

| span 名 | 出现时机 | 关键属性 |
|---|---|---|
| `invoke_agent Strands Agents` | 每次 `agent(prompt)` 调用 | `gen_ai.agent.name`、`system_prompt`、usage |
| `execute_event_loop_cycle` | 每个事件循环周期 | `event_loop.cycle_id` |
| `chat` | 每次模型调用 | `gen_ai.request.model`、`gen_ai.usage.*` tokens |
| `execute_tool <name>` | 每次工具执行 | 工具入参/出参、耗时 |

所有 span 归属**同一 trace_id**（同一 prompt = 一条完整 trace），可直接进 Jaeger/Tempo 看瀑布图。

## 二、六大 Pitfall（本次全部踩过）

### 1. 自定义 TracerProvider 必须手动设为全局 ⚠️

```python
provider = SDKTracerProvider(resource=Resource.create({"service.name": "x"}))
trace_api.set_tracer_provider(provider)          # ← 少了这行，span 全是 no-op
telemetry = StrandsTelemetry(tracer_provider=provider)
```

`StrandsTelemetry` 传入自定义 provider 时**不会**调用 `set_tracer_provider`（只有
`tracer_provider=None` 时才会）。而 strands 内部 `get_tracer()` 读的是
**全局** provider → 不手动 set，你的 InMemory/OTLP exporter 永远收不到 span。

### 2. OTel provider 全局单例，不可 override

进程内 `set_tracer_provider(非 API provider)` 只成功一次，后续静默失败并警告。
→ 设计测试时所有 pattern 共享一个 provider，各自 `add_span_processor`。

### 3. BatchSpanProcessor 需要 `force_flush`

```python
exporter.force_flush()     # ❌ 自定义 exporter 上调用 = no-op（只 flush 自己，而 span 在 processor 的队列里）
provider.force_flush()     # ✅ 遍历所有 processor 冲刷
processor.force_flush()    # ✅ 或指定 processor
```

### 4. ConsoleSpanExporter 的 stdout 在 import 时绑定

```python
class ConsoleSpanExporter:
    def __init__(self, out=sys.stdout): ...   # 默认值在类定义时求值！
```

→ `contextlib.redirect_stdout` **捕获不到**它的输出。必须显式：

```python
buf = io.StringIO()
telemetry.setup_console_exporter(out=buf)    # ✅
```

### 5. `AgentResult` 没有 `accumulated_usage`

usage 的路径是：**`result.metrics.agent_invocations[-1].usage`**
（`Usage` 可能是 dict 或对象，字段 camelCase：`inputTokens/outputTokens/totalTokens`）。
`result.accumulated_usage` 是旧版/Bedrock 文档的字段，1.53 中不存在。

### 6. OTLP exporter 是可选依赖

`from opentelemetry.exporter.otlp.proto.http.trace_exporter import ...` 需要显式安装：

```bash
uv add opentelemetry-exporter-otlp-proto-http
```

`StrandsTelemetry.setup_otlp_exporter(endpoint=...)` 内部对失败只 `logger.exception`
不抛异常 → 端点不可达**不会**影响 agent 主流程（Pattern 6 已验证）。

## 三、Patterns 速查（对应 demo_otel.py）

| # | Pattern | 结论 |
|---|---|---|
| 1 | Console Span Exporter | span JSON 流式打印，含全部 gen_ai 属性 |
| 2 | In-memory 层次断言 | 6 spans 同 trace；`chat`/`execute_tool`/cycle 齐全 |
| 3 | Metrics console | `setup_meter(enable_console_exporter=True)` 可用 |
| 4 | `trace_attributes` 透传 | `Agent(trace_attributes={"team":...})` → 出现在每个 span |
| 5 | Batch + flush | `force_flush` 前 0 条、之后 N 条 → 批量语义成立 |
| 6 | OTLP 容错 | 端点挂掉 agent 照常跑完 |

## 四、生产接 Jaeger/Tempo 的最小配置（推演，未在本机起服务）

```bash
# Jaeger all-in-one:  docker run -p 16686:16686 -p 4317:4317 jaegertracing/all-in-one
export OTEL_SERVICE_NAME=your-agent
# agent 侧:
#   StrandsTelemetry().setup_otlp_exporter()   # 默认读 OTEL_EXPORTER_OTLP_ENDPOINT
```

建议 attributes 惯例：`team` / `stage` / `user_id`（脱敏后）/ `project`，
通过 `Agent(trace_attributes=...)` 注入，跨 span 自动透传。

## 五、与 Codex 的可观测性对比

| | Strands | Codex CLI |
|---|---|---|
| 标准 | ✅ OTel（trace+metrics），属性带 GenAI semconv（`gen_ai.*`）可进 Langfuse/OTel 原生 UI | OTel 导出走 `otel` 配置（OTLP），面向自研后端 |
| 自定义属性 | `trace_attributes` 一等公民 | `--config otel.*` |
| 本地验证 | InMemoryExporter / Console | 需起 collector 或用 app-server 日志 |

**Strands 在本地模型 + OTel 生态这块更顺**（GenAI semconv 是 2024 后才定型的，
新项目直接受益；Codex 的事件流更偏"应用内事件协议"）。
