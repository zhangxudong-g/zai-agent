# 🧪 Quickstart 运行记录（实测发现）

> 测试日期：2026-08-26
> 脚本：`tests/demo_quickstart.py`
> 环境：Windows + Python 3.12 + strands-agents 1.53.0 + Ollama qwen3.8:27b

---

## ✅ 全部 6 节通过

```
[Section 1] letter_counter 自定义 @tool       PASS (5.4s)
[Section 2] stream_async + async for 流式    PASS (6.5s)
[Section 3] Callback Handler 自定义          PASS (1.3s)
[Section 4] Debug 日志 (logging.getLogger)   PASS (1.9s)
[Section 5] AgentResult 字段探索             PASS (3.3s)
[Section 6] 字符串 model_id 直接传入         PASS (0.1s)
─────────────────────────────
结果：6/6 passed
```

总耗时约 18 秒（含多轮回合）。

---

## 🔍 实测发现的 3 个真实问题

### 1. Windows GBK 终端的 Unicode 问题（**Section 2**）

**症状**：
```
UnicodeEncodeError: 'gbk' codec can't encode character '−' in position 1:
illegal multibyte sequence
```

**原因**：qwen3 模型回复中包含 `−` (U+2212)、`×` (U+00D7) 等数学符号，Windows 默认 GBK 编码无法打印。

**修复**：在流式输出里加 `.replace("−", "-").replace("×", "*")`，或强制 `PYTHONIOENCODING=utf-8`。

**影响**：所有用 `print(event["data"])` 的代码都需要这个 fallback；Linux/macOS 默认 UTF-8 没事。

---

### 2. `strands_tools.shell` 在 Windows 不可用（**Section 3**）

**症状**：
```
ModuleNotFoundError: No module named 'termios'
```

**原因**：`strands_tools.shell` 用 `termios` 做终端原始模式，仅 Unix 系支持。

**修复**：演示里改用 `calculator` 替代 `shell`。Callback 机制本身与工具无关，只是示例里官方用了 shell。

**经验**：
- **跨平台 demo 不要用 shell/editor 这类 Unix-only 工具**
- 若必须 shell，跑在 WSL 或 Docker 中
- 真正的生产 Agent 用 `DockerSandbox` 把 shell 隔离在容器里

---

### 3. `AgentResult` 字段路径在 1.53.0 与官方 Quickstart 不一致（**Section 5**）

**官方 Quickstart 写法**（来自 `strandsagents.com/docs/user-guide/quickstart/python/`）：
```python
result = agent("...")
print(result.metrics.get_summary())
# 暗示可直接访问 result.accumulated_usage 等
```

**1.53.0 真实结构**（`strands/agent/agent_result.py`）：
```python
@dataclass
class AgentResult:
    stop_reason: StopReason
    message: Message
    metrics: EventLoopMetrics   # ← 所有用量数据都在这里
    state: Any
    interrupts: Sequence[Interrupt] | None
    structured_output: BaseModel | None
    checkpoint: Checkpoint | None
```

**真实字段路径**：
| 想看的字段 | 1.53.0 正确路径 |
|-----------|---------------|
| 输入/输出 tokens | `result.metrics.accumulated_usage.inputTokens` |
| 累计耗时 | `result.metrics.accumulated_metrics.latencyMs` |
| 每个工具调用次数 | `result.metrics.tool_metrics[name].call_count` |
| 循环次数 | `result.metrics.cycle_count` |
| 追踪 | `result.metrics.traces` |
| 最近一次输入 token | `result.context_size`（property） |
| 预计下轮 token | `result.projected_context_size`（property） |

> ⚠️ **重要**：教程里 `result.accumulated_usage` / `result.tool_usage` / `result.total_cycles` 这种**直接属性访问**在 1.53.0 会 `AttributeError`。所有这些字段都在 `result.metrics.*` 下。

**Ollama 的特殊坑**：
```
"usage": {
  "inputTokens": null,   ← Ollama 不返回标准 token 计数
  "outputTokens": null,
  "totalTokens": null
}
```
需要看 `cycle_durations` 或在模型层做估算。

---

## 📦 产出物

### `tests/demo_quickstart.py`

**用法**：
```bash
# 全部 6 节
python tests/demo_quickstart.py --all

# 单节
python tests/demo_quickstart.py --section 3
```

**对应官方 Quickstart 章节**：
| Section | 官方对应章节 |
|---------|------------|
| 1 | "Installation" + "Create your first agent" + "Use custom tools" |
| 2 | "Async Iterators" |
| 3 | "Callback Handlers" |
| 4 | "Debug logs" |
| 5 | "Agent execution results"（metrics.get_summary） |
| 6 | "Model providers" + 字符串 model_id |

---

## 🎯 阶段 0 总结

- ✅ 6/6 官方示例全部跑通
- ✅ 发现并修复 3 个真实问题（已写进脚本注释）
- ✅ 验证 Ollama 端点可达（DNS 解析慢但能跑）
- ✅ 验证 SDK 1.53.0 实际 API 与官方文档的偏差

**阶段 0 完成，可进入阶段 1（补齐缺失章节）或直接进阶段 2（Hooks/Middleware 实战）**。