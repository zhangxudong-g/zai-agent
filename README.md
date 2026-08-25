# Strands Agents SDK + Ollama Qwen3.8 PoC

## 概述

把 `claude-agent-sdk` 迁到 **Strands Agents SDK** 的最小验证项目。Strands 是 AWS 主推的开源 Agent SDK（Apache-2.0），提供 `strands.Agent` 纯 Python in-process API。

> 配套设计文档: `../claude-agent/docs/MIGRATION_STRANDS.md`(完整迁移方案)
> 横向对比: `../claude-agent/docs/MIGRATION_COMPARISON.md`(5 个候选对比)

## Strands 的独特卖点

| 维度 | Strands |
|---|---|
| **沙箱实施成本** | **最低** —— 一行 `event.cancel_tool = "reason"`，工具本身完全不动 |
| **运行时** | 纯 Python in-process，无子进程 |
| **长期维护** | **AWS 主推**，长期稳定 |
| **TypeScript 同步生态** | ✅ `@strands-agents/sdk`（未来前端/Web 化无缝衔接） |
| **流式 API** | `agent.stream_async()` 异步生成器，yield `dict` 事件 |

## 与 `claude-agent` 的差异

| 维度 | claude-agent | strands-agent (本 PoC) |
|---|---|---|
| SDK | `claude-agent-sdk` (子进程 CLI) | `strands-agents` (纯 Python in-process) |
| 模型协议 | Ollama Anthropic-compat (`/v1/messages`) | `OllamaModel(host=, model_id=)` 走 Ollama 原生 `/api/chat` |
| 沙箱 | PreToolUse hook 改 `decision: block` | **`BeforeToolCallEvent.cancel_tool = "reason"`**（一行否决） |
| Hooks | SDK 子进程 4 个回调 | `agent.add_hook(cb, EventType)` 或 `HookProvider` 类 |
| 流式事件 | 手写 SSE 解析 | `agent.stream_async()` yield `dict`（含 `data` / `current_tool_use` / `result` / `force_stop`） |
| 流式类型安全 | — | ⚠️ dict（字段名写错运行时才 None） |

## 项目结构

```
strands-agent/
├── pyproject.toml                # strands-agents[ollama]
├── .env.example                  # 配置模板
├── README.md                     # 本文件
│
├── src/strands_poc/
│   ├── __init__.py
│   ├── config.py                 # env → Config dataclass
│   ├── llm.py                    # OllamaModel factory
│   ├── tools.py                  # @tool 装饰器 ReadTool / GlobTool / GrepTool / WriteTool / EditTool
│   ├── security.py               # WorkspaceSandboxHook (BeforeToolCallEvent.cancel_tool)
│   ├── trace.py                  # SessionLogger (JSONL 镜像)
│   ├── stream.py                 # dict event → StreamChunk 翻译
│   ├── agent.py                  # Agent 类 (run / run_async / run_streaming)
│   └── main.py                   # argparse CLI
│
├── prompts/
│   ├── init.txt                  # 系统提示 (项目结构分析)
│   └── qa001_*.txt               # 示例用户任务 (并发编辑分析)
│
├── workspace/sample_project/     # 测试样本 (Java 项目，含 QA001 并发 bug)
├── sessions/                     # JSONL 输出 (gitignored)
└── tests/
    └── test_smoke.py             # 冒烟测试 (不依赖 Ollama)
```

## 安装

```bash
cd D:\agent_harness_sdk_demo\strands-agent
uv sync
cp .env.example .env
# 编辑 .env 调整 OLLAMA_BASE_URL 等
```

## 运行

### 冒烟测试 (不需 Ollama)

```bash
uv run pytest -v
```

### 最小端到端 (需 Ollama 在跑)

```bash
uv run python -m strands_poc.main \
  --workspace ./workspace/sample_project \
  --prompt "请列出项目的目录结构。"
```

### 流式输出

```bash
uv run python -m strands_poc.main \
  --workspace ./workspace/sample_project \
  --prompt "请分析项目中可能的并发问题。" \
  --stream
```

### 验证 JSONL 输出

```bash
# 用 ../claude-agent 的 validator 校验 (保持 schema 一致)
uv run --project ../claude-agent python ../claude-agent/src/agent/validate_session.py sessions/<sid>.jsonl
```

## PoC 验证清单

| # | 验证项 | 命令 | 期望结果 |
|---|---|---|---|
| 1 | 依赖装得上 | `uv sync` | 成功 |
| 2 | SDK 可导入 | `uv run python -c "from strands import Agent; from strands.models.ollama import OllamaModel"` | 不报错 |
| 3 | Config 加载 | `uv run pytest tests/test_smoke.py::test_config_*` | 绿 |
| 4 | 沙箱拦截路径外 | `uv run pytest tests/test_smoke.py::test_sandbox_*` | 绿 |
| 5 | JSONL schema 一致 | `uv run pytest tests/test_smoke.py::test_session_logger_*` | 绿 |
| 6 | 端到端 (需 Ollama) | 见上方"最小端到端" | 产生 JSONL + final answer |

## 已知限制 (PoC 阶段)

- **未跑真实端到端** —— Ollama 连接未在本机验证
- **`strands.models.ollama` 可能不存在** —— 早期版本可能在 `strands.models.ollama.OllamaModel` 或 `strands.models.ollama.Ollama`，以 `pip show strands-agents` 后实际目录为准
- **`BeforeToolCallEvent` 字段名可能漂移** —— 早期文档显示 `tool_use["name"]` / `tool_use["input"]`，实际可能是 `tool_use["name"]` / `tool_use["input"]`，对照源码校对
- **流式事件是 dict** —— 字段名写错运行时才 None，需手写 stub 或断言

## 参考

- 迁移方案: `../claude-agent/docs/MIGRATION_STRANDS.md`
- Strands Agents: https://strandsagents.com/
- Hooks 文档: https://strandsagents.com/docs/user-guide/concepts/agents/hooks/
- Ollama 模型: https://strandsagents.com/docs/user-guide/concepts/model-providers/ollama
- GitHub: https://github.com/strands-agents/sdk-python