# CLI 工具打包设计

## 目标

将 strands-agent 打包为命令行全局工具，实现：
- 全局安装后 `strands-agent` 命令在任意目录可用
- 自动以当前工作目录为 agent workspace
- 兼容现有 REPL 交互模式和单次问答模式

## 设计决策

| 决策项 | 选择 |
|--------|------|
| 打包方式 | uv tool / pip install |
| CLI 入口 | `strands-agent` |
| Workspace 默认值 | 当前目录 (`.`) |
| 兼容性 | 保留原有 `uv run agent` 方式 |

## 实现方案

### 1. pyproject.toml 修改

```toml
[project]
name = "strands-agent"
version = "0.1.0"
description = "Local AI coding assistant with Strands + Ollama"
requires-python = ">=3.12"
dependencies = [
    "strands-agents>=1.0.0",
    "ollama>=0.3.0",
    "python-dotenv>=1.0.0",
    "click>=8.0.0",
]

[project.scripts]
strands-agent = "strands_poc.main:run_cli"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

### 2. CLI 入口函数

在 `src/strands_poc/main.py` 添加 `run_cli()` 函数：

```python
import os
from pathlib import Path
from .agent import Agent
from .config import Config
from .trace import SessionLogger

def run_cli():
    """CLI entry point for global installation."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Strands Agent - Local AI Coding Assistant")
    parser.add_argument("prompt", nargs="?", help="Prompt to execute")
    parser.add_argument("-i", "--interactive", action="store_true", help="Start REPL mode")
    parser.add_argument("--workspace", default=".", help="Workspace directory (default: current directory)")
    parser.add_argument("--env-file", default=".env", help="Environment file")
    
    args = parser.parse_args()
    
    # Use current directory as workspace if not specified
    workspace = Path(args.workspace).resolve()
    if not workspace.exists():
        workspace = Path.cwd()
    
    config = Config(
        agent_workspace=str(workspace),
        env_file=args.env_file,
    )
    
    # 复用现有的 REPL 逻辑
```

### 3. 安装方式

```bash
# 方式 1: uv tool (推荐)
uv tool install .

# 方式 2: uv pip install
uv pip install .

# 方式 3: pip install
pip install .
```

### 4. 使用示例

```bash
# 安装后全局可用
cd /any/project/directory
strands-agent "分析这个项目"           # 单次问答，当前目录为 workspace
strands-agent -i                       # 交互模式 REPL

# 查看帮助
strands-agent --help
```

## 文件改动清单

| 文件 | 改动内容 |
|------|----------|
| `pyproject.toml` | 添加 `[project.scripts]` 和 metadata |
| `src/strands_poc/main.py` | 添加 `run_cli()` 函数 |

## 向后兼容

- 原有的 `uv run agent` 方式继续有效
- `.env` 配置文件中 `AGENT_WORKSPACE` 仍可使用，但会被 CLI `--workspace` 参数覆盖
- 所有现有功能和工具保持不变

## 验证步骤

1. `uv tool install .` 安装成功
2. 在任意目录运行 `strands-agent "test"` 无报错
3. workspace 确认为当前目录
4. REPL 模式 `strands-agent -i` 正常工作
5. `uv run agent` 原有方式仍可用
