# Zai Agent

> 🤖 基于 Ollama 本地模型的智能代码助手，安全、隐私、可扩展

**Zai Agent** 是一个基于 [Strands Agents SDK](https://strandsagents.com/) 和 [Ollama](https://ollama.ai/) 的通用代码助手。无需云服务，数据不离开本地。

## ✨ 特性

| 特性 | 说明 |
|------|------|
| 🔒 **本地优先** | 完全离线运行，数据不离开你的机器 |
| 🛡️ **双层沙箱** | 路径验证 + 执行隔离，防范恶意操作 |
| 💬 **连续对话** | REPL 模式保持上下文，多轮协作 |
| 📡 **实时流式** | 即时看到模型思考过程和工具调用 |
| ⚡ **Shell 集成** | 原生支持 git、ls、find 等命令 |

## 🚀 快速开始

### 环境要求

- Python 3.12+
- [Ollama](https://ollama.ai/) 运行中

### 安装

```bash
pip install git+https://github.com/zhangxudong-g/zai-agent.git
```

### 运行

```bash
# 交互模式
zai

# 单次问答
zai "分析项目结构"

# 指定工作目录
zai --workspace ./my-project
```

### 卸载

```bash
# 自动卸载（包括用户数据 ~/.zai/）
zai -uninstall
```

## 📁 数据目录

Zai 使用 `~/.zai/` 作为统一的数据目录：

```
~/.zai/
├── config/
│   └── .env           # 配置文件（首次运行自动创建）
├── sessions/          # 会话日志
├── workspace/         # 默认工作目录
└── logs/             # 运行日志
```

### 查看信息

```bash
zai --info
```

## ⚙️ 配置

首次运行会自动创建 `~/.zai/config/.env`，或在运行时手动编辑：

```bash
# ~/.zai/config/.env
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:1.7b
AGENT_WORKSPACE=~/.zai/workspace
```

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 服务地址 |
| `OLLAMA_MODEL` | `qwen3:1.7b` | 模型名称 |
| `AGENT_WORKSPACE` | `~/.zai/workspace` | 工作目录 |
| `ZAI_HOME` | `~/.zai` | Zai 主目录 |
| `ZAI_DEBUG_OLLAMA` | `0` | 设为 `1` 启用调试日志 |

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

```bash
# 克隆项目
git clone https://github.com/zhangxudong-g/zai-agent.git
cd zai-agent

# 安装依赖
uv sync

# 交互模式
uv run zai

# 单次运行
uv run zai "分析项目结构"
```

## 📖 使用示例

```
╭─ zai · qwen3:1.7b · C:\Users\admin\.zai\workspace · session 20250908_100000_0001
╰─ 📁 C:\Users\admin\.zai  📝 C:\Users\admin\.zai\sessions\20250908_100000_0001.jsonl  ──  /help /clear /exit

[1] > 分析这个项目

🤔 思考中...
  🔧 file_tree(max_depth=3)
  🔧 read(file_path="README.md")
这个项目是...

✓ (3.2s)

[2] > /exit
```

## 📁 项目结构

```
zai-agent/
├── src/zai/
│   ├── __init__.py      # 包入口
│   ├── agent.py         # Agent 核心逻辑
│   ├── config.py        # 配置管理
│   ├── paths.py         # 目录路径管理
│   ├── main.py          # CLI 入口
│   └── ...
├── tests/               # 测试用例
├── scripts/             # 辅助脚本
└── ...
```

## 🧪 测试

```bash
uv run pytest -v
```

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

[MIT License](LICENSE)
