# Strands Agents SDK + Ollama

基于 Strands Agents SDK 的 Agent Harness，支持 Ollama 本地模型。

## 快速开始

### 1. 环境准备

- Python 3.12+
- [Ollama](https://ollama.ai/) 运行中（默认 `localhost:11434`）
- 下载模型：`ollama pull qwen3:7b`

### 2. 安装

```bash
cd D:/agent_harness_sdk_demo/strands-agent
uv sync
cp .env.example .env
```

编辑 `.env` 配置（如需）：
```bash
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:7b
AGENT_WORKSPACE=./workspace/sample_project
```

### 3. 运行

```bash
# 冒烟测试（无需 Ollama）
uv run pytest -v

# 交互式运行
uv run python -m strands_poc.main \
  --workspace ./workspace/sample_project \
  --prompt "列出项目目录结构"

# 流式输出
uv run python -m strands_poc.main \
  --workspace ./workspace/sample_project \
  --prompt "分析并发问题" \
  --stream
```

## 核心功能

### Agent 运行模式

| 模式 | 方法 | 说明 |
|------|------|------|
| 同步 | `agent.run()` | 等待完成返回结果 |
| 异步 | `agent.run_async()` | 异步协程 |
| 流式 | `agent.run_streaming()` | yield 实时事件 |

### 沙箱安全

两层隔离设计：

| 层 | 实现 | 作用 |
|----|------|------|
| 路径层 | `WorkspaceSandboxHook` | 阻止访问 workspace 外的路径 |
| 执行层 | `Sandbox` (host/docker/ssh) | 运行时进程隔离 |

默认启用路径层，零配置即可防御路径穿越。

### 内置工具

| 工具 | 功能 |
|------|------|
| `read` | 读取文件内容 |
| `glob` | 文件模式匹配 |
| `grep` | 内容搜索 |
| `file_tree` | 目录树生成 |
| `outline` | 代码大纲 |
| `write` | 写入文件 |
| `edit` | 编辑文件（diff 模式） |

## 项目结构

```
strands-agent/
├── src/strands_poc/
│   ├── agent.py          # Agent 主类
│   ├── config.py         # 配置管理
│   ├── llm.py            # Ollama 模型封装
│   ├── tools.py          # 工具定义
│   ├── security.py       # 路径层沙箱
│   ├── sandbox.py        # 执行层沙箱
│   ├── stream.py         # 流式事件
│   └── main.py           # CLI 入口
├── prompts/              # 任务提示模板
├── workspace/            # Agent 工作目录
├── sessions/             # JSONL 会话日志
└── tests/                # 测试用例
```

## 配置说明

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 地址 |
| `OLLAMA_MODEL` | `qwen3:7b` | 模型名称 |
| `OLLAMA_AUTH_TOKEN` | `ollama` | 认证令牌 |
| `AGENT_WORKSPACE` | `./workspace/sample_project` | 工作目录 |
| `SESSION_LOG_DIR` | `./sessions` | 会话日志目录 |
| `ALLOWED_TOOLS` | `read,glob,grep,file_tree,outline,write,edit` | 启用的工具 |
| `EXECUTION_SANDBOX` | `host` | 执行沙箱模式（host/docker/ssh） |

### 生产环境隔离

```bash
# Docker 隔离
EXECUTION_SANDBOX=docker
SANDBOX_CONTAINER=strands-sandbox

# SSH 隔离
EXECUTION_SANDBOX=ssh
SANDBOX_SSH_HOST=remote-host
SANDBOX_SSH_USER=admin
```

## 常见问题

**Q: 工具调用被拦截？**  
检查 `ALLOWED_TOOLS` 是否包含该工具名称。

**Q: 路径越界错误？**  
确保操作的文件在 `AGENT_WORKSPACE` 目录内。

**Q: Ollama 连接失败？**  
确认 Ollama 服务运行中：`curl http://localhost:11434/api/tags`

## 参考

- [Strands Agents SDK](https://strandsagents.com/)
- [Ollama 模型配置](https://strandsagents.com/docs/user-guide/concepts/model-providers/ollama)
- [Hooks 系统](https://strandsagents.com/docs/user-guide/concepts/agents/hooks/)
