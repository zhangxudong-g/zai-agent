# Strands Agents PoC 验证笔记

> 项目位置: `D:\agent_harness_sdk_demo\strands-agent\`
> 配套设计文档: `D:\agent_harness_sdk_demo\claude-agent\docs\MIGRATION_STRANDS.md`
> 横向对比: `D:\agent_harness_sdk_demo\claude-agent\docs\MIGRATION_COMPARISON.md`

## 1. PoC 目标

把 `claude-agent-sdk` 替换成 **Strands Agents SDK** 的最小可运行验证。验证 6 件事:

| # | 验证项 | 状态 | 验证方式 |
|---|---|---|---|
| 1 | `strands-agents[ollama]` 能装上 | ⏳ 待运行 | `uv sync` |
| 2 | SDK 可导入(关键类齐全) | ⏳ 待运行 | `uv run python -c "from strands import Agent; ..."` |
| 3 | WorkspaceSandboxHook 拦截路径外 | ✅ 已单元测 | `uv run pytest tests/test_smoke.py::test_sandbox_*` |
| 4 | 沙箱只否决 write/edit 不影响 read-only | ✅ 已单元测 | `test_sandbox_ignores_readonly_tools` |
| 5 | JSONL schema 与现状一致 | ✅ 已单元测 | `test_session_logger_writes_well_formed_jsonl` |
| 6 | StreamConsumer 正确翻译 dict 事件 | ✅ 已单元测 | 7 个 `test_consumer_*` |
| 7 | 端到端 (需 Ollama) | ⏳ 待运行 | 见 §3 |

## 2. 项目结构

```
strands-agent/
├── pyproject.toml                 # strands-agents[ollama]
├── .env.example                   # 配置模板
├── .gitignore
│
├── src/zai/
│   ├── __init__.py
│   ├── config.py                  # env → Config dataclass
│   ├── llm.py                     # OllamaModel factory (版本容错)
│   ├── tools.py                   # @tool 装饰器的 Read/Glob/Grep/Write/Edit
│   ├── security.py                # WorkspaceSandboxHook (BeforeToolCallEvent.cancel_tool)
│   ├── trace.py                   # SessionLogger (JSONL 镜像)
│   ├── stream.py                  # StreamConsumer (dict → StreamChunk)
│   ├── agent.py                   # Agent 类 (run / run_async / run_streaming) + JsonlTraceHook
│   └── main.py                    # argparse CLI
│
├── prompts/
│   ├── init.txt                   # 系统提示 (项目结构分析)
│   └── qa001_concurrent_edit_analysis.txt
│
├── workspace/sample_project/
│   ├── README.md
│   └── src/main/java/com/example/
│       ├── ChildRecord.java
│       ├── ChildEditService.java     # ← 含 QA001 并发 bug
│       ├── ChildEditController.java
│       ├── ChildRepository.java
│       └── ChildPatch.java
│   └── src/test/java/com/example/
│       └── ChildEditServiceTest.java
│
├── sessions/                      # JSONL 输出 (gitignored)
│
├── tests/
│   ├── __init__.py
│   └── test_smoke.py              # 14 个测试, 全部无需 Ollama 或 strands SDK
│
└── docs/
    └── POC_NOTES.md               # 本文件
```

## 3. 如何运行 PoC

### 3.1 安装依赖

```bash
cd D:\agent_harness_sdk_demo\strands-agent
uv sync
```

预期输出:`Resolved N packages` + `Installed N packages`,无报错。
若报 `strands-agents` 找不到 → 检查 pyproject.toml 版本约束或换 `>=0.1.0`。

### 3.2 配置环境变量

```bash
cp .env.example .env
# 编辑 .env:OLLAMA_BASE_URL / OLLAMA_MODEL / AGENT_WORKSPACE
```

### 3.3 跑冒烟测试(不需 Ollama / strands SDK)

```bash
uv run pytest -v
```

预期:14 个测试**全部通过**(SDK 导入相关的 3 个会 skip 因为 SDK 未装,但其他 11 个应该全绿)。

### 3.4 跑最小端到端(需 Ollama 在跑)

```bash
uv run python -m zai.main \
  --workspace ./workspace/sample_project \
  --prompt "请列出项目的目录结构。" \
  --stream
```

预期:
- 终端流式输出模型回复
- `sessions/<sid>.jsonl` 写入 ~10 个事件
- WorkspaceSandboxHook 自动拦截 Write/Edit 到 workspace 外的尝试

### 3.5 验证 JSONL

```bash
# 用 claude-agent 的 validator 校验 schema 一致性
uv run --project ../claude-agent python ../claude-agent/src/agent/validate_session.py sessions/<sid>.jsonl
```

预期:`Result: PASS`(若有 schema 偏差,见 §4 风险)。

## 4. 已知风险与缓解

### 4.1 SDK API 漂移

**风险**: Strands SDK 仍在快速迭代,某些方法名/参数可能与文档不符。

**影响**:
- `llm.py`:`OllamaModel` 类名可能改为 `Ollama`(代码已做 fallback)
- `agent.py`:`agent.add_hook()` / `agent.hooks` 二选一(代码已做 fallback)
- `agent.py`:`agent.invoke_async()` / `agent(prompt)` / `agent.stream_async()` 三种调用方式(代码已做 fallback)
- `security.py`:`BeforeToolCallEvent.tool_use` / `.tool` 属性名漂移(代码已做 fallback)
- `stream.py`:dict 事件 key 名漂移(代码 `consumer.feed` 已做容错)

**缓解**:
1. 跑 `uv run python -c "from strands.models.ollama import OllamaModel; help(OllamaModel.__init__)"`
2. 用 `inspect.signature()` 打印关键方法签名
3. 必要时调整代码以匹配当前 SDK 版本

### 4.2 HookProvider 注册方式

**风险**: 新版 Strands 可能用 `agent.add_hook(provider)`,旧版用 `agent.hooks.add_callback()`。

**当前实现**: `agent.py` 优先尝试 `add_hook`,再尝试 `agent.hooks.register_hooks`,再 fallback 到 `_hooks` 属性。

**验证**: 跑端到端看 hook 是否被调用(JSONL 里应有 tool_call_start/end 行)。

### 4.3 流式事件 key 名

**风险**: 文档说 `current_tool_use.toolUseId`,实际可能是 `id` 或其他。

**当前实现**: `StreamConsumer` 已支持 `toolUseId` 或 `id` 两种写法。

### 4.4 流式 / 同步 API 选择

**风险**: `stream_async()` 可能在某些 Strands 版本不存在。

**当前实现**: `agent.run_streaming` 优先尝试 `stream_async`,fallback 到 `agent(prompt)`(同步 + 异步执行)。

### 4.5 端到端超时

**风险**: qwen3.8:27b 在 Ollama 上首次调用可能很慢。

**缓解**: 测试用更小的 `qwen3:7b` 模型。

## 5. PoC 决策记录

### 5.1 为什么用 Strands 而非 OpenHands / Agno

参考 `docs/MIGRATION_COMPARISON.md` 第 5 节 —— 综合最优是 **OpenHands + Strands 并列第一**,本 PoC 选 Strands 因为:
1. **AWS 主推** —— 长期维护有保障(SWE-AI 商业公司背书)
2. **TypeScript 同步生态** —— `@strands-agents/sdk` 可未来做 Web/前端
3. **沙箱实施成本最低** —— 一行 `event.cancel_tool = "reason"`,工具不重写
4. **SDK API 设计简洁** —— `strands.Agent` + `agent.add_hook()` 心智模型清晰

### 5.2 为什么保留 JSONL schema 不变

参考 `docs/MIGRATION_STRANDS.md` 第 10 节 —— 我们的 `validate_session.py` 是 schema 单一来源。**保持 schema 不变**让 88 个 pytest 全绿、迁移风险最低。

### 5.3 为什么用 dict 事件而非 dataclass

Strands 的 dict 事件是**设计选择**(不是 bug)。虽然失去类型安全,但通过 `StreamConsumer` 集中翻译 + 容错,**对外仍提供 dataclass `StreamChunk`**,main.py 仍按原 schema 工作。

## 7. PoC 验证结果(待填)

### 7.1 依赖安装

```
[ ] uv sync 成功
[ ] 报错及原因:_______________
```

### 7.2 SDK 导入

```
[ ] from strands import Agent, tool 成功
[ ] from strands.hooks import BeforeToolCallEvent, AfterToolCallEvent 成功
[ ] from strands.models.ollama import OllamaModel 或 Ollama 成功
[ ] 实际签名与代码假设一致
[ ] 偏差及调整:_______________
```

### 7.3 单元测试

```
[ ] uv run pytest -v 至少 11 个绿(SDK 导入相关 3 个 skip)
[ ] 失败用例及原因:_______________
```

### 7.4 端到端(Ollama)

```
[ ] 跑通最小 hello world prompt
[ ] 跑通"列项目结构"任务
[ ] 跑通 QA001 并发分析任务
[ ] sessions/<sid>.jsonl 通过 validate_session.py
[ ] 流式输出正常
[ ] 沙箱拦截路径外写入
```

## 8. 下一步

PoC 验证通过后:
1. **正式迁移**: 按 `docs/MIGRATION_STRANDS.md` §13 的 12 步走完完整迁移
2. **复用 main.py**: 把 claude-agent 的 main.py 搬过来(已设计为接口兼容)
3. **复用 validate_session.py**: JSONL schema 完全一致,无需改动
4. **回归测试**: 跑完 claude-agent 的 88 个测试 + 新增的 Strands-specific 测试

如果 PoC 验证**失败**(API 漂移、性能问题等):
- 退回到 OpenHands(同等综合分,能力更完整)
- 或退回到 Agno(纯 Python + dataclass 类型安全)
- 详见 `docs/MIGRATION_COMPARISON.md` §5