# Strands Agent

基于 Strands Agents SDK + Ollama 的通用代码助手。

## 特性

- 🤖 **Ollama 本地模型** - 无需云服务，数据不离开本地
- 🔒 **沙箱安全** - 两层隔离，防止恶意操作
- 💬 **连续对话** - REPL 模式支持多轮对话
- 📡 **流式输出** - 实时看到模型思考过程
- 🔄 **自动重试** - 连接失败自动重试 3 次

## 快速开始

### 1. 环境准备

- Python 3.12+
- [Ollama](https://ollama.ai/) 运行中
- 下载模型：`ollama pull qwen3:7b`

### 2. 安装

```bash
cd D:/agent_harness_sdk_demo/strands-agent
uv sync
cp .env.example .env
```

### 3. 配置

编辑 `.env`：
```bash
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:7b
AGENT_WORKSPACE=./workspace
```

### 4. 运行

```bash
# 交互模式（连续对话）
uv run agent

# 单次问答
uv run agent "分析 src 目录的代码结构"

# 同步模式（禁用流式）
uv run agent "列出文件" --sync
```

## 使用示例

```bash
$ uv run agent
============================================================
Strands Agent REPL - 连续对话模式
============================================================
命令:
  /exit, /quit, /q  - 退出
  /help              - 显示帮助
  /clear             - 清屏
============================================================

[1] 你: 分析这个项目的结构
[Agent 分析中...]

[2] 你: 找到的代码有什么问题？
[Agent 回答...]

[3] 你: /exit
再见!
```

## 命令行参数

| 参数 | 说明 |
|------|------|
| `-i, --interactive` | 启动交互式 REPL（无参数时默认） |
| `--sync` | 禁用流式输出 |
| `--workspace PATH` | 指定工作目录 |
| `--max-retries N` | 连接失败重试次数（默认 3） |
| `--env-file FILE` | 指定 .env 文件 |

## 配置项

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 服务地址 |
| `OLLAMA_MODEL` | `qwen3:7b` | 模型名称 |
| `OLLAMA_AUTH_TOKEN` | `ollama` | 认证令牌 |
| `AGENT_WORKSPACE` | `./workspace` | Agent 工作目录 |
| `SESSION_LOG_DIR` | `./sessions` | 会话日志目录 |
| `ALLOWED_TOOLS` | `read,glob,grep,file_tree,outline,write,edit` | 启用的工具 |
| `EXECUTION_SANDBOX` | `host` | 执行沙箱模式 |

### 工具列表

| 工具 | 功能 |
|------|------|
| `read` | 读取文件内容 |
| `glob` | 按模式搜索文件 |
| `grep` | 文件内容搜索 |
| `file_tree` | 目录树结构 |
| `outline` | Python 代码大纲 |
| `write` | 写入文件 |
| `edit` | 编辑文件 |

### 沙箱模式

```bash
# 主机模式（默认，无隔离）
EXECUTION_SANDBOX=host

# Docker 隔离
EXECUTION_SANDBOX=docker
SANDBOX_CONTAINER=strands-sandbox

# SSH 隔离
EXECUTION_SANDBOX=ssh
SANDBOX_SSH_HOST=remote-host
SANDBOX_SSH_USER=admin
```

## 项目结构

```
strands-agent/
├── src/strands_poc/
│   ├── agent.py       # Agent 主类
│   ├── config.py      # 配置管理
│   ├── llm.py         # Ollama 模型封装
│   ├── tools.py       # 工具定义
│   ├── security.py    # 路径层沙箱
│   ├── sandbox.py     # 执行层沙箱
│   ├── stream.py      # 流式事件处理
│   └── main.py        # CLI 入口
├── workspace/         # Agent 工作目录
├── sessions/          # JSONL 会话日志
└── tests/            # 测试用例
```

## 常见问题

**Q: 连接 Ollama 失败？**
```bash
# 检查 Ollama 是否运行
curl http://localhost:11434/api/tags

# 重试机制：默认自动重试 3 次
uv run agent "问题" --max-retries 5
```

**Q: 工具调用被拦截？**
检查 `ALLOWED_TOOLS` 环境变量，确保包含该工具名称。

**Q: 路径越界错误？**
确保操作的文件在 `AGENT_WORKSPACE` 目录内。

**Q: 想看详细日志？**
会话日志保存在 `sessions/` 目录，按时间戳命名。

## 测试

```bash
# 运行所有测试
uv run pytest -v

# 运行指定测试
uv run pytest tests/test_streaming_output.py -v
```

## 参考

- [Strands Agents SDK](https://strandsagents.com/)
- [Ollama 官网](https://ollama.ai/)
- [Strands Hooks 文档](https://strandsagents.com/docs/user-guide/concepts/agents/hooks/)
