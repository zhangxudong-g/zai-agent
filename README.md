# Strands Agent

基于 Strands Agents SDK + Ollama 的通用代码助手。

## 特性

- 🤖 **Ollama 本地模型** - 无需云服务，数据不离开本地
- 🔒 **沙箱安全** - 两层隔离，防止恶意操作
- 💬 **连续对话** - REPL 模式支持多轮对话，上下文保持
- 📡 **流式输出** - 实时看到模型思考过程和工具调用
- 🔄 **自动重试** - 连接失败自动重试 3 次
- ⚡ **Shell 命令** - 执行 git、ls 等命令

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

### 3. 运行

```bash
# 交互模式（连续对话）
uv run zai

# 单次问答
uv run zai "分析项目结构"

# 指定工作目录
uv run zai --workspace ./my-project
```

### 全局安装（推荐）

安装后可在任意目录使用 `zai` 命令，自动以当前目录为 workspace：

```bash
# 使用 uv 安装（推荐）
uv tool install .

# 或使用 pip
pip install .

# 卸载
uv tool uninstall strands-agent
```

#### 使用示例

```bash
# 任意目录直接运行，当前目录自动作为 workspace
cd /my/project
zai "分析这个项目"

# 交互模式
zai -i

# 指定 workspace（可选，会覆盖当前目录）
zai --workspace ./other-project "分析代码"
```

## 使用示例

```bash
$ zai
╭─ Strands Agent ──────────────────────────────
│ Model:     qwen3.8:27b
│ Workspace: ./workspace
│ Session:   20260903_100000_0001
│ Mode:      REPL (连续对话)
╰────────────────────────────────────────────

╭─ Strands Agent REPL ─────────────────────────
│ /help   显示帮助
│ /clear  清屏
│ /exit   退出
╰─────────────────────────────────────────────

[1] > 分析这个项目

🤔 思考中...
  🔧 file_tree(max_depth=3)
  🔧 read(file_path="README.md")
  🔧 grep(pattern="TODO")
这个项目是...

✓ (3.2s)

[2] > 最近有哪些提交？
  🔧 shell(command="git log -5 --oneline")
abc123 feat: add new feature
def456 fix: resolve bug
...

✓ (0.5s)

[3] > /exit
```

## 工具列表

| 工具 | 功能 |
|------|------|
| `read` | 读取文件内容，支持 offset/limit/max_bytes |
| `glob` | 按模式搜索文件路径 |
| `grep` | 文件内容正则搜索 |
| `file_tree` | 目录树结构（JSON） |
| `outline` | Python 代码大纲（类/函数签名） |
| `shell` | 执行 shell 命令（git, ls, find 等） |
| `write` | 写入文件 |
| `edit` | 编辑文件 |

## 命令行参数

| 参数 | 说明 |
|------|------|
| `-i, --interactive` | 启动交互式 REPL |
| `--sync` | 禁用流式输出 |
| `--workspace PATH` | 指定工作目录 |
| `--max-retries N` | 连接失败重试次数（默认 3） |
| `--env-file FILE` | 指定 .env 文件 |

## 配置

### 环境变量 (.env)

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 服务地址 |
| `OLLAMA_MODEL` | `qwen3:7b` | 模型名称 |
| `AGENT_WORKSPACE` | `./workspace` | 工作目录 |
| `SESSION_LOG_DIR` | `./sessions` | 会话日志目录 |
| `ALLOWED_TOOLS` | `read,glob,grep,file_tree,outline,shell,write,edit` | 启用的工具 |
| `EXECUTION_SANDBOX` | `host` | 执行沙箱模式 |

### Shell 工具

仅允许白名单中的命令：
```
git, ls, find, grep, cat, head, tail, tree, 
python, node, npm, docker, ...
```

安全限制：输出限 5000 字符，超时 30 秒。

### 沙箱模式

```bash
# 主机模式（默认）
EXECUTION_SANDBOX=host

# Docker 隔离
EXECUTION_SANDBOX=docker
SANDBOX_CONTAINER=strands-sandbox

# SSH 隔离
EXECUTION_SANDBOX=ssh
SANDBOX_SSH_HOST=remote-host
```

## 项目结构

```
strands-agent/
├── src/strands_poc/
│   ├── agent.py       # Agent 主类
│   ├── config.py      # 配置管理
│   ├── llm.py         # Ollama 模型封装
│   ├── tools.py       # 工具定义
│   ├── security.py     # 路径层沙箱
│   ├── sandbox.py      # 执行层沙箱
│   ├── stream.py       # 流式事件处理
│   └── main.py        # CLI 入口
├── workspace/          # Agent 工作目录
├── sessions/          # JSONL 会话日志
└── tests/            # 测试用例
```

## 常见问题

**Q: 连接 Ollama 失败？**
```bash
curl http://localhost:11434/api/tags
uv run zai "问题" --max-retries 5
```

**Q: 工具调用被拦截？**
检查 `ALLOWED_TOOLS` 是否包含该工具。

**Q: 路径越界错误？**
确保操作的文件在 `AGENT_WORKSPACE` 目录内。

**Q: 想看详细日志？**
会话日志保存在 `sessions/` 目录。

## 测试

```bash
uv run pytest -v
```

## 参考

- [Strands Agents SDK](https://strandsagents.com/)
- [Ollama](https://ollama.ai/)
