# Strands Agent + Ollama

通用代码助手，基于 Strands Agents SDK + Ollama 本地模型。

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

编辑 `.env`（如需）：
```bash
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:7b
AGENT_WORKSPACE=./workspace
```

### 3. 运行

```bash
# 冒烟测试
uv run pytest -v

# 单次运行（默认流式输出）
uv run agent "列出当前目录结构"

# 交互式 REPL（连续对话，默认流式）
uv run agent
uv run agent -i

# 同步模式（禁用流式）
uv run agent "列出目录" --sync
```

## 核心功能

### 工具集

| 工具 | 功能 |
|------|------|
| `read` | 读取文件内容 |
| `glob` | 文件模式匹配 |
| `grep` | 内容搜索 |
| `file_tree` | 目录树生成 |
| `outline` | 代码大纲 |
| `write` | 写入文件 |
| `edit` | 编辑文件 |

### 沙箱安全

| 层 | 实现 |
|----|------|
| 路径层 | `WorkspaceSandboxHook` — 阻止访问 workspace 外路径 |
| 执行层 | `Sandbox` (host/docker/ssh) — 运行时进程隔离 |

## 项目结构

```
strands-agent/
├── src/strands_poc/
│   ├── agent.py      # Agent 主类
│   ├── config.py     # 配置管理
│   ├── llm.py        # Ollama 模型封装
│   ├── tools.py      # 工具定义
│   ├── security.py   # 路径层沙箱
│   ├── sandbox.py    # 执行层沙箱
│   ├── stream.py     # 流式事件
│   └── main.py       # CLI 入口
├── workspace/        # Agent 工作目录
├── sessions/         # JSONL 会话日志
└── tests/            # 测试用例
```

## 配置说明

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 地址 |
| `OLLAMA_MODEL` | `qwen3:7b` | 模型名称 |
| `AGENT_WORKSPACE` | `./workspace` | 工作目录 |
| `SESSION_LOG_DIR` | `./sessions` | 会话日志 |
| `ALLOWED_TOOLS` | `read,glob,grep,file_tree,outline,write,edit` | 启用的工具 |

### CLI 参数

| 参数 | 说明 |
|------|------|
| `--sync` | 禁用流式输出 |
| `--max-retries` | 连接失败时最大重试次数（默认 3）|

## 交互模式

启动 REPL 后可连续对话，Agent 会记住上下文。

```
$ uv run agent -i
============================================================
Strands Agent REPL - 连续对话模式
============================================================
命令:
  /exit, /quit, /q  - 退出
  /help              - 显示帮助
  /clear             - 清屏
============================================================

[1] 你: 分析这段代码的问题
...
[2] 你: 如何修复？
...
```

### REPL 命令

| 命令 | 说明 |
|------|------|
| `/exit`, `/quit`, `/q` | 退出 |
| `/help` | 显示帮助 |
| `/clear` | 清屏 |

## 常见问题

**Q: 工具调用被拦截？**  
检查 `ALLOWED_TOOLS` 是否包含该工具。

**Q: 路径越界错误？**  
确保操作的文件在 `AGENT_WORKSPACE` 目录内。

**Q: Ollama 连接失败？**  
确认 Ollama 运行中：`curl http://localhost:11434/api/tags`

## 参考

- [Strands Agents SDK](https://strandsagents.com/)
- [Ollama 模型配置](https://strandsagents.com/docs/user-guide/concepts/model-providers/ollama)
