# CLI 工具打包实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打包 strands-agent 为全局命令行工具，安装后 `strands-agent` 在任意目录可用，自动以当前目录为 workspace

**Architecture:** 修改 pyproject.toml 添加 CLI 入口点，main.py 复用现有 main() 函数逻辑，workspace 默认值改为当前目录

**Tech Stack:** hatchling, argparse (已有), Pathlib (已有)

---

## 文件改动清单

| 文件 | 改动 |
|------|------|
| `pyproject.toml` | 添加 `strands-agent = "zai.main:run_cli"` 入口 |
| `src/zai/main.py` | 添加 `run_cli()` 函数 |

---

## Task 1: 修改 pyproject.toml 添加 CLI 入口

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 添加 strands-agent 入口点**

在 `[project.scripts]` 部分添加新行：

```toml
[project.scripts]
agent = "zai.main:main"
strands-agent = "zai.main:run_cli"
```

- [ ] **Step 2: 提交**

```bash
git add pyproject.toml
git commit -m "feat: add strands-agent CLI entry point"
```

---

## Task 2: 在 main.py 添加 run_cli() 函数

**Files:**
- Modify: `src/zai/main.py`

- [ ] **Step 1: 在 main() 函数前添加 run_cli() 函数**

在 `main()` 函数定义之前（约第 100 行处），添加以下代码：

```python
def run_cli(argv: list[str] | None = None) -> int:
    """CLI entry point for global installation.

    Automatically uses current directory as workspace when --workspace is not specified.
    This function wraps main() with workspace defaults to current directory.
    """
    args = build_parser().parse_args(argv)

    # Use current directory as default workspace if not specified
    if args.workspace is None:
        args.workspace = Path.cwd()

    # Delegate to main() for all the existing logic
    return main(argv)
```

注意：这个函数复用现有的 `main()` 逻辑，只需要设置默认 workspace 即可。

- [ ] **Step 2: 验证代码语法**

```bash
cd D:/agent_harness_sdk_demo/strands-agent
uv run python -c "from zai.main import run_cli; print('OK')"
```

Expected output: `OK`

- [ ] **Step 3: 提交**

```bash
git add src/zai/main.py
git commit -m "feat: add run_cli() for global CLI installation"
```

---

## Task 3: 测试安装

**Files:**
- None (验证步骤)

- [ ] **Step 1: 本地安装测试**

```bash
cd D:/agent_harness_sdk_demo/strands-agent
uv tool install .
```

Expected: 安装成功，无报错

- [ ] **Step 2: 测试 strands-agent --help**

```bash
strands-agent --help
```

Expected output:
```
usage: Strands Agent CLI
...

positional arguments:
  prompt              Prompt (optional; reads from stdin if omitted).

options:
  -i, --interactive  Start interactive REPL mode.
  --workspace PATH   Workspace directory (defaults to $AGENT_WORKSPACE in .env).
  --sync             Disable streaming output (default: streaming enabled).
  --max-retries N    Max retry attempts on connection errors (default: 3).
  --env-file FILE    Path to .env file (default: .env).
```

- [ ] **Step 3: 测试 workspace 自动使用当前目录**

在非 workspace 目录运行：

```bash
cd /tmp
strands-agent --help 2>&1 | head -5
```

Expected: 不报错，能显示帮助信息

- [ ] **Step 4: 清理测试安装**

```bash
uv tool uninstall strands-agent
```

---

## Task 4: 更新 README 文档

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 添加全局安装章节**

在 README.md 的 "快速开始" 章节之后，添加：

```markdown
## 全局安装

安装后可在任意目录使用 `strands-agent` 命令：

```bash
# 使用 uv 安装（推荐）
uv tool install .

# 或使用 pip
pip install .
```

### 使用示例

```bash
# 任意目录直接运行，当前目录自动作为 workspace
cd /my/project
strands-agent "分析这个项目"

# 交互模式
strands-agent -i

# 指定 workspace（可选，会覆盖当前目录）
strands-agent --workspace ./other-project "分析代码"
```
```

- [ ] **Step 2: 提交**

```bash
git add README.md
git commit -m "docs: add global installation instructions"
```

---

## 验证清单

- [ ] `uv tool install .` 成功
- [ ] `strands-agent --help` 正常显示
- [ ] 在任意目录运行 `strands-agent "test"` 不报错
- [ ] workspace 确认为当前目录（而非 .env 中的默认值）
- [ ] 原有 `agent` 命令仍可用
- [ ] `uv run agent` 方式仍可用
