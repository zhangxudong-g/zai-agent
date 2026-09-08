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
| 🎨 **彩色 TUI** | ANSI 颜色、Markdown 渲染、进度指示 |
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
# 交互模式（推荐）
zai

# 单次问答
zai "分析项目结构"

# 指定工作目录
zai --workspace ./my-project
```

### 卸载

```bash
zai -uninstall    # 自动清理 pip 包 + ~/.zai/ 数据
```

## 📁 数据目录

Zai 使用 `~/.zai/` 作为统一的数据目录：

```
~/.zai/
├── config/
│   └── .env           # 配置文件（首次运行自动创建）
├── sessions/          # 会话日志
├── workspace/         # 默认工作目录
├── logs/              # 运行日志
└── history            # REPL 历史记录
```

### 查看信息

```bash
zai --info
```

## ⚙️ 配置

首次运行会自动创建 `~/.zai/config/.env`，或手动编辑：

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

| 工具 | 功能 | 特性 |
|------|------|------|
| `read` | 读取文件 | 自动编码检测（UTF-8/GBK/UTF-16），大文件保护 |
| `glob` | 路径搜索 | - |
| `grep` | 内容搜索 | 正则匹配，文件模式过滤 |
| `file_tree` | 目录树 | 支持 JSON 输出，深度限制 |
| `outline` | Python 大纲 | AST 提取类/函数 |
| `shell` | 执行命令 | 自动 Unix → Windows 翻译，stderr/exit code 分离 |
| `write` | 写入文件 | - |
| `edit` | 编辑文件 | 多匹配报错、原子写入、`replace_all`、`dry_run` |
| `diff` | 预览变更 | 不写入，仅返回 diff |

### Edit 使用示例

```python
# 基本替换（唯一匹配）
edit("test.py", "foo", "bar")

# 替换所有
edit("test.py", "foo", "bar", replace_all=True)

# 仅预览不写入
edit("test.py", "foo", "bar", dry_run=True)
```

## ⌨️ REPL 快捷键

| 快捷键 | 功能 |
|--------|------|
| `↑ / ↓` | 浏览历史记录 |
| `Ctrl+R` | 模糊搜索历史 |
| `Ctrl+L` | 清屏 |
| `Ctrl+C` | 取消当前操作（运行中）/ 退出（空闲） |
| `Ctrl+D` | EOF 退出 |

### REPL 命令

| 命令 | 功能 |
|------|------|
| `/help` | 显示帮助 |
| `/info` | 显示配置信息 |
| `/clear` | 清屏 |
| `/exit` | 退出 |

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

### 代码修改后

```bash
uv sync              # 同步依赖
uv run pytest -v     # 运行测试
uv run ruff check .  # 代码检查
uv run ruff format . # 格式化
```

## 📖 使用示例

```
╭─ zai · qwen3:1.7b · C:\Users\admin\.zai\workspace · session 20250908_100000_0001
╰─ 📁 C:\Users\admin\.zai  📝 ...\sessions\20250908_100000_0001.jsonl  ──  /help /clear /exit

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
│   ├── config.py        # 配置管理（~/.zai/ 优先）
│   ├── paths.py         # 目录路径管理
│   ├── tui.py           # 终端 UI（颜色、spinner、box）
│   ├── repl.py          # REPL（prompt_toolkit）
│   ├── tools.py         # 9 个工具实现
│   ├── security.py      # 路径层沙箱
│   ├── sandbox.py       # 执行层沙箱
│   ├── stream.py        # 流式事件处理
│   └── main.py          # CLI 入口
├── tests/               # 测试用例（117 个）
└── scripts/             # 辅助脚本
```

## 🧪 测试

```bash
# 全部测试
uv run pytest -v

# 仅 TUI
uv run pytest tests/test_tui.py -v

# 仅工具增强
uv run pytest tests/test_tool_enhancements.py -v
```

## ❓ 常见问题

**Q: Ollama 连接失败？**
```bash
# 检查 Ollama 服务
curl http://localhost:11434/api/tags

# 增加重试次数
zai "问题" --max-retries 5
```

**Q: 文件是 GBK 编码乱码？**
`read` 工具会自动检测编码，输出包含 `[encoding: gbk]` 提示。

**Q: edit 多次匹配失败？**
使用 `replace_all=True` 或在 `old_string` 中提供更多上下文使其唯一。

**Q: 想预览修改而不实际写入？**
使用 `diff` 工具或 `edit(..., dry_run=True)`。

**Q: 工具调用路径越界？**
确保操作的文件在 `AGENT_WORKSPACE` 目录内（由沙箱强制）。

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！请参阅 [CONTRIBUTING.md](CONTRIBUTING.md)。

未来计划请参阅 [TODO.md](TODO.md)。

## 📄 许可证

[MIT License](LICENSE)

## 🔗 参考

- [Strands Agents SDK](https://strandsagents.com/)
- [Ollama](https://ollama.ai/)
- [uv 包管理器](https://github.com/astral-sh/uv)