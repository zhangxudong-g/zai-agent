# Zai Agent

> 🤖 基于 Ollama 本地模型的智能代码助手，安全、隐私、可扩展

[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![strands-agents](https://img.shields.io/badge/strands--agents-sdk-v1.0.0-purple)](https://strandsagents.com/)

**Zai Agent** 是一个基于 [Strands Agents SDK](https://strandsagents.com/) 和 [Ollama](https://ollama.ai/) 的通用代码助手。无需云服务，数据不离开本地，让 AI 编程更加安全私密。

## ✨ 特性

| 特性 | 说明 |
|------|------|
| 🔒 **本地优先** | 完全离线运行，数据不离开你的机器 |
| 🛡️ **双层沙箱** | 路径验证 + 执行隔离，防范恶意操作 |
| 💬 **连续对话** | REPL 模式保持上下文，多轮协作 |
| 📡 **实时流式** | 即时看到模型思考过程和工具调用 |
| 🔄 **智能重试** | 连接失败自动重试，稳定可靠 |
| ⚡ **Shell 集成** | 原生支持 git、ls、find 等命令 |

## 🚀 快速开始

### 环境要求

- Python 3.12+
- [Ollama](https://ollama.ai/) 运行中
- 推荐的模型: `qwen3:7b` 或 `qwen3:27b`

### 安装方式

**方式一：pip 安装（推荐）**

```bash
pip install git+https://github.com/zhangxudong-g/zai-agent.git
```

**方式二：从源码安装**

```bash
# 克隆项目
git clone https://github.com/zhangxudong-g/zai-agent.git
cd zai-agent
pip install .
```

### 卸载

```bash
pip uninstall zai-agent
```

### 运行

```bash
# 交互模式（推荐）
zai

# 单次问答
zai "分析项目结构"

# 指定工作目录
zai --workspace ./my-project

# 查看帮助
zai --help
```

## 📖 使用示例

```bash
$ zai
╭─ Zai Agent ──────────────────────────────
│ Model:     qwen3:8b
│ Workspace: ./workspace
│ Session:   20250908_100000_0001
│ Mode:      REPL
╰────────────────────────────────────────

[1] > 分析这个项目

🤔 思考中...
  🔧 file_tree(max_depth=3)
  🔧 read(file_path="README.md")
这个项目是...

✓ (3.2s)

[2] > /exit
```

## 🛠️ 工具集

| 工具 | 功能 | 示例 |
|------|------|------|
| `read` | 读取文件内容 | `read("src/main.py", offset=1, limit=50)` |
| `glob` | 按模式搜索文件 | `glob("**/*.py")` |
| `grep` | 内容正则搜索 | `grep(pattern="TODO", file_pattern="*.py")` |
| `file_tree` | 目录树结构 | `file_tree(max_depth=3)` |
| `outline` | Python 代码大纲 | `outline("src/utils.py")` |
| `shell` | 执行 shell 命令 | `shell(command="git status")` |
| `write` | 写入文件 | `write("path", content)` |
| `edit` | 编辑文件 | `edit("path", old_text, new_text)` |

## 👨‍💻 本地开发

### 环境要求
- Python 3.12+
- [Ollama](https://ollama.ai/) 运行中

### 启动开发

```bash
# 克隆项目
git clone https://github.com/zhangxudong-g/zai-agent.git
cd zai-agent

# 安装依赖
uv sync

# 交互模式启动
uv run zai

# 或单次运行
uv run zai "分析项目结构"
```

### 停止运行
- 按 `Ctrl+C` 或输入 `/exit` 退出

### 代码修改后
```bash
# 依赖更新后重新同步
uv sync

# 运行测试
uv run pytest -v

# 代码检查
uv run ruff check .
```

## 📁 统一目录结构

Zai 使用 `~/.zai/` 作为统一的数据目录：

```
~/.zai/
├── config/
│   └── .env           # 配置文件
├── sessions/          # 会话日志
├── workspace/         # 默认工作目录
└── logs/             # 运行日志
```

### 查看 Zai 信息
```bash
zai --info
```

### 自定义目录
```bash
# 使用自定义 ZAI_HOME 目录
export ZAI_HOME=/path/to/my-zai

# 或通过配置文件
# 编辑 ~/.zai/config/.env
export ZAI_HOME=/custom/path
```

### 调试选项

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ZAI_DEBUG_OLLAMA` | `0` | 设为 `1` 启用 Ollama 请求调试日志 |

启用后，日志保存在 `~/.zai/sessions/ollama-debug.jsonl`

## ⚙️ 配置

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 服务地址 |
| `OLLAMA_MODEL` | `qwen3:1.7b` | 模型名称 |
| `AGENT_WORKSPACE` | `~/.zai/workspace` | 工作目录 |
| `SESSION_LOG_DIR` | `~/.zai/sessions` | 会话日志目录 |
| `ALLOWED_TOOLS` | 全部工具 | 启用的工具列表 |
| `EXECUTION_SANDBOX` | `host` | 执行沙箱模式 |
| `ZAI_HOME` | `~/.zai` | Zai 主目录 |
| `ZAI_CONFIG` | `~/.zai/config/.env` | 配置文件路径 |

### 沙箱模式

```bash
# 主机模式（默认）
EXECUTION_SANDBOX=host

# Docker 隔离
EXECUTION_SANDBOX=docker
SANDBOX_CONTAINER=zai-sandbox

# SSH 隔离
EXECUTION_SANDBOX=ssh
SANDBOX_SSH_HOST=remote-host
```

## 📁 项目结构

```
zai-agents/
├── src/zai/
│   ├── __init__.py      # 包入口
│   ├── agent.py         # Agent 核心逻辑
│   ├── config.py        # 配置管理
│   ├── llm.py           # Ollama 模型封装
│   ├── tools.py         # 工具定义
│   ├── security.py      # 路径层沙箱
│   ├── sandbox.py       # 执行层沙箱
│   ├── stream.py        # 流式事件处理
│   └── main.py          # CLI 入口
├── tests/               # 测试用例
├── docs/                # 文档
├── prompts/             # 提示词模板
├── LICENSE              # MIT 许可证
├── CONTRIBUTING.md      # 贡献指南
└── CHANGELOG.md         # 变更日志
```

## 🧪 测试

```bash
uv run pytest -v
```

## ❓ 常见问题

**Q: 连接 Ollama 失败？**
```bash
# 检查 Ollama 服务
curl http://localhost:11434/api/tags

# 增加重试次数
uv run zai "问题" --max-retries 5
```

**Q: 工具调用被拦截？**
检查 `ALLOWED_TOOLS` 环境变量是否包含该工具。

**Q: 路径越界错误？**
确保操作的文件在 `AGENT_WORKSPACE` 目录内。

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！请参阅 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 📄 许可证

本项目基于 [MIT License](LICENSE) 开源。

## 🔗 参考

- [Strands Agents SDK](https://strandsagents.com/)
- [Ollama](https://ollama.ai/)
- [uv 包管理器](https://github.com/astral-sh/uv)
