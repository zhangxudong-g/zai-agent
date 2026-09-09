# Strands Sandbox —— 官方文档完整中文版

> 源文档：<https://strandsagents.com/docs/user-guide/concepts/sandbox/>
> 抓取时间：2026-08-26
> 本地 SDK 版本：strands-agents 1.53.0（**部分章节与官方最新版有差异，章节末尾有对比说明**）
> 配套代码：`tests/demo_sandbox.py --all`（8/8 通过）

本文档是以下 4 篇官方文档的合并译文 + 与本地实现的差异对比：

| 子文档 | 翻译章节 |
|--------|---------|
| Sandbox Overview | 第二章「Sandbox 是什么」+ 第三章「快速开始」 |
| Available Sandboxes | 第四章「内置 Sandbox」 |
| Building a Custom Sandbox | 第五章「自定义 Sandbox」 |
| Vended Tools（部分） | 第六章「与 Vended Tools 的关系」 |

---

## 目录

1. [核心问题](#一核心问题为什么需要-sandbox)
2. [Sandbox 是什么](#二sandbox-是什么)
3. [快速开始](#三快速开始)
4. [内置 Sandbox](#四内置-sandbox)
5. [自定义 Sandbox](#五自定义-sandbox)
6. [与 Vended Tools 的关系](#六与-vended-tools-的关系)
7. [与官方文档的最佳实践清单](#七最佳实践清单)
8. [本地 1.53.0 vs 官方最新版的差异](#八本地-1530-vs-官方最新版的差异)

---

## 一、核心问题：为什么需要 Sandbox

Agent 的工具（特别是 `file_editor` 和 `shell`）默认**直接在主机上执行**——等于把 LLM 提升到了宿主机的最高权限。

**风险场景**：

| 风险 | 触发 | 后果 |
|------|------|------|
| 模型被 prompt 诱导 | 恶意用户在多轮对话中慢慢"教"模型执行 `rm -rf /` | 数据灾难性丢失 |
| 工具 bug | 边界条件没考虑到 | 文件误删 |
| prompt injection | 让模型去 curl 一个恶意 URL 并执行 | 远程下载并植入代码 |
| 环境越权 | agent 临时需要 root，但部署在普通容器中 | 越权失败或越权成功 |

**Sandbox 的解决思路**：把执行操作与 agent 进程解耦，让执行跑在受控环境（容器、远程 VM、微虚拟机）里，agent 进程本身仍在你的基础设施内运行。

---

## 二、Sandbox 是什么

### 原文定义

> A `Sandbox` gives the agent an execution environment for these operations while keeping the agent's core process (model calls, hooks, state) decoupled.

**直译**：Sandbox 给 agent 一个执行环境，让 agent 的核心进程（model call、hooks、state）保持解耦。

### 两种沙箱架构

1. **整个 agent 跑在沙箱里**——部署层面的事，不是 SDK 特性
2. **agent 通过沙箱执行**——Strands 采用的模式

```
┌─────────────────────────────────────────┐
│  Agent runtime（你的基础设施，宿主机）       │
│                                         │
│  ┌──────────┐                            │
│  │ Agent    │ ← model calls              │
│  │  loop    │ ← hooks                    │
│  └────┬─────┘ ← state                    │
│       │                                  │
│  ┌────▼─────────────────┐                │
│  │ sandbox_shell         │                │
│  │ sandbox_file_editor   │ ← 工具层        │
│  └────┬─────────────────┘                │
└───────┼──────────────────────────────────┘
        │ 标准接口（execute / read / write）
┌───────▼──────────────────────────────────┐
│  Sandbox（你预置的执行环境）                │
│  - Docker 容器                            │
│  - 远程 SSH 主机                          │
│  - 自定义后端（VM、云代码 API...）          │
└─────────────────────────────────────────┘
```

**关键设计原则**：Agent 进程跑在宿主机（保留 hooks/state 可观测性），只把**执行操作**外包给 sandbox。

---

## 三、快速开始

### 最小用法

```python
from strands import Agent
from strands.sandbox.docker import DockerSandbox

agent = Agent(sandbox=DockerSandbox("my-container-id"))
agent("List all files inside the current directory")
```

Agent 自动注册两个工具：

| 工具名 | 作用 | 状态 |
|--------|------|------|
| `sandbox_shell` | 执行 shell 命令（每次全新 shell，无状态） | 自动注入 |
| `sandbox_file_editor` | 查看、创建、编辑文件（绝对路径 + view/create/str_replace/insert） | 自动注入 |

### ⚠️ 最重要的安全警告（官方原文）

> **Omitting the `sandbox` parameter runs the agent's command and file tools directly on the host with the full permissions of the agent process.**

直译：省略 sandbox = 把 agent 进程的全部权限交给 LLM，等于给它 root 权限。

**生产环境必须传 sandbox。** TypeScript 可以显式 `sandbox: false` 表示"我知道我在做什么"。

---

## 四、内置 Sandbox

Strands 提供两个内置后端：`DockerSandbox`（本地容器）和 `SshSandbox`（远程主机）。

### 4.1 DockerSandbox

通过 `docker exec` 在主机上的容器内执行操作。

**关键事实**：
- 容器必须**已经运行**——Strands **不会**自动创建容器
- 走 `docker exec`，不保持持久连接
- 每次命令走一次 exec

**构造参数**：

| 参数 | Python 命名 | TS 命名 | 类型 | 默认 | 含义 |
|------|------------|---------|------|------|------|
| 容器 | `container` | `container` | `str` | required | 容器 ID 或名称（必须已运行） |
| 工作目录 | `working_dir` | `workingDir` | `str \| None` | 容器默认 | 容器内的工作目录 |
| 用户 | `user` | `user` | `str \| None` | 容器默认 | `uid`、`uid:gid` 或用户名 |

**示例**：

```python
from strands import Agent
from strands.sandbox.docker import DockerSandbox

sandbox = DockerSandbox(
    "agent-workspace",
    working_dir="/workspace",
    user="1000:1000",  # 非 root
)
agent = Agent(sandbox=sandbox)
agent("Run the test suite and summarize any failures")
```

### 4.2 SshSandbox

通过 SSH 在远程主机上执行。每次命令启动一个**新的** `ssh` 进程，不复用连接。

**强约束**：
- 主机必须可用**基于密钥的身份验证**
- `BatchMode` 强制启用——密码提示会直接失败，不会阻塞
- 不支持持久连接

**构造参数**：

| 参数 | Python 命名 | TS 命名 | 类型 | 默认 | 含义 |
|------|------------|---------|------|------|------|
| 主机 | `host` | `host` | `str` | required | `user@host` 或 IP |
| 工作目录 | `working_dir` | `workingDir` | `str` | required | 远程工作目录 |
| 私钥 | `identity_file` | `identityFile` | `str \| None` | `None` | 私钥路径 |
| 端口 | `port` | `port` | `int` | `22` | SSH 端口 |
| 主机密钥 | `allow_unknown_hosts` | `allowUnknownHosts` | `bool` | `False` | `True` 时跳过 `StrictHostKeyChecking` |
| 额外选项 | `ssh_options` | `sshOptions` | `list[str] \| None` | `None` | `-o` 参数 |
| 不安全逃生 | `allow_unsafe_ssh_options` | `allowUnsafeSshOptions` | `bool` | `False` | 跳过白名单校验，**极危险** |

**示例**：

```python
from strands import Agent
from strands.sandbox.ssh import SshSandbox

sandbox = SshSandbox(
    "ubuntu@10.0.1.5",
    working_dir="/home/ubuntu/workspace",
    identity_file="~/.ssh/agent_key",
)
agent = Agent(sandbox=sandbox)
agent("Check disk usage and list running processes")
```

### 4.3 SSH 选项白名单（关键安全机制）

**默认行为**：`SshSandbox` 只允许已知安全的 SSH 选项（连接调优、加密、认证）。**未知选项在构造时就抛错**——阻止模型生成的 `ProxyCommand` / `LocalCommand` 等指令在本地主机上跑命令。

**危险逃生**：`allow_unsafe_ssh_options=True` 会绕过白名单。

> **官方原话**：> Setting `allowUnsafeSshOptions: true` will bypass this allowlist, allowing arbitrary SSH options through, **including directives that run commands on the local host**. Only enable this when the options are controlled by you, **never with model-generated or untrusted input**.

### 4.4 两个内置后端的对比

| 维度 | DockerSandbox | SshSandbox |
|------|--------------|-----------|
| 执行机制 | `docker exec` | 每次新开 `ssh` |
| 前置条件 | 容器已在运行 | 主机可达，密钥认证已配 |
| 认证 | 无 | 强制密钥（`BatchMode`） |
| 持久连接 | 否 | 否 |
| 环境变量 | 构造时不设，按命令 `env` 传入 | 同 |

### 4.5 直接编程访问 Sandbox（不经过 Agent）

常用于 Agent 运行前后的"输入预处理 / 输出校验"：

```python
import asyncio
from strands import Agent
from strands.sandbox.docker import DockerSandbox


async def main():
    agent = Agent(sandbox=DockerSandbox("my-container-id"))

    # 1) 写入输入
    await agent.sandbox.write_text("/workspace/input.csv", "id,value\n1,42\n")

    # 2) 调用 Agent
    agent("Summarize /workspace/input.csv and write the summary to /workspace/out.txt")

    # 3) 读回结果
    result = await agent.sandbox.execute("cat /workspace/out.txt")
    print(result.exit_code, result.stdout)


asyncio.run(main())
```

非流式便捷方法：`execute`、`execute_code`、`read_text`、`write_text`。

### 4.6 流式输出

```python
from strands.sandbox import ExecutionResult, StreamChunk


async def stream_example():
    sandbox = DockerSandbox("my-container-id")

    async for chunk in sandbox.execute_streaming("npm run build"):
        if isinstance(chunk, StreamChunk):
            print(chunk.data, end="")  # 边产生边输出
        elif isinstance(chunk, ExecutionResult):
            print(f"\nexit code: {chunk.exit_code}")


asyncio.run(stream_example())
```

流式协议：**先 yield 一系列 `StreamChunk`，最后一次 yield 必须是 `ExecutionResult`**。

### 4.7 内置后端的常见陷阱

1. **DockerSandbox 不创建容器**——调用前自己用 `docker run` 起好。
2. **SshSandbox 不复用连接**——每次命令重新走一次 SSH。
3. **SshSandbox 不接受密码**——必须配密钥。
4. **环境变量不在构造时设**——必须按命令传 `env={...}`。
5. **`allow_unsafe_ssh_options=True` 是逃生口**——绝不用在模型生成或不受信输入上。
6. **未知 SSH 选项会在构造时抛错**——这是默认安全行为，别关掉。
7. **TS 的 `workingDir` ≠ Python 的 `working_dir`**——命名差异注意。

---

## 五、自定义 Sandbox

当目标环境不是 Docker / SSH（微虚拟机、云代码 API、托管运行时等）时，需要写自己的后端。Agent loop、模型、工具都不变——只需补齐执行操作和文件访问的方法。

### 5.1 推荐路径：继承 `PosixShellSandbox`

只需实现一个方法 `execute_streaming`，自动获得：

- **代码执行**（base64 编码 heredoc 通过管道送给解释器）
- **文件读写**（base64 over shell）
- **目录列出**（`ls -1ap`）

`DockerSandbox` 和 `SshSandbox` 都继承自 `PosixShellSandbox`。

**完整 Python 示例**：

```python
import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from strands.sandbox import PosixShellSandbox
from strands.sandbox.types import ExecutionResult, StreamChunk


class FirecrackerSandbox(PosixShellSandbox):
    """Run commands in a Firecracker microVM addressed by id."""

    def __init__(self, vm_id: str) -> None:
        self.vm_id = vm_id

    async def execute_streaming(
        self,
        command: str,
        *,
        timeout: float | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[StreamChunk | ExecutionResult, None]:
        proc = await asyncio.create_subprocess_exec(
            "fc-exec",
            self.vm_id,
            "sh",
            "-c",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if stdout:
            yield StreamChunk(data=stdout.decode(), stream_type="stdout")
        if stderr:
            yield StreamChunk(data=stderr.decode(), stream_type="stderr")
        yield ExecutionResult(
            exit_code=proc.returncode or 0,
            stdout=stdout.decode(),
            stderr=stderr.decode(),
        )
```

### 5.2 vending 自定义工具

通过 `get_tools()` 把 `make_shell` / `make_file_editor` 绑在自己的 sandbox 上，自动获得与内置后端一致的 `sandbox_shell` / `sandbox_file_editor`：

```python
from strands.types.tools import AgentTool
from strands.vended_tools import make_file_editor, make_shell


class MyCustomSandbox(PosixShellSandbox):
    # ... execute_streaming 实现略 ...

    def get_tools(self) -> list[AgentTool]:
        return [
            make_file_editor(sandbox=self, name="sandbox_file_editor"),
            make_shell(sandbox=self, name="sandbox_shell"),
        ]
```

### 5.3 直接继承抽象基类（完整控制权）

当环境提供原生 API（非 shell）时，可以直接继承 `Sandbox`，**自己实现全部 6 个抽象方法**：

- `execute_streaming`
- `execute_code_streaming`
- `read_file`
- `write_file`
- `remove_file`
- `list_files`

**官方建议**：只要后端能执行 `sh -c`，就优先选 `PosixShellSandbox`——少写很多代码。只有当原生 API 比"包装成 shell 命令"更高效或更安全时，才走裸基类。

### 5.4 安全提醒（关键）

> The security comes from the environment you provision, **not from the interface itself**.

**直译**：安全来自于你预置的环境，**不是接口本身**。

接口只是路由操作，不限制操作范围。Agent 通过 `execute_streaming` 能触及的范围 = 它在环境中能触及的范围。以 root 运行、挂载宿主机的容器**不是真正的边界**——即使你用了 `Sandbox` 接口。

按任务所需的**最小权限**配置环境，并把环境配置当作真正的安全控制。

---

## 六、与 Vended Tools 的关系

### 6.1 工具的双层覆盖

Vended Tools 是 SDK 自带的预构建工具（`shell`、`file_editor`、`http_request`、`notebook` 等）。它们的命名空间在 `strands.vended_tools` 下。

**两类 shell 工具并存**——非常容易踩坑：

| 工具 | 路由 | 状态 |
|------|------|------|
| Python `strands.vended_tools.shell` / TS `makeShell` | 通过 **agent 的 sandbox** | **无状态**（每次新 shell） |
| Python `bash` / TS `bash`（主机版） | **绕过 sandbox**，直接在主机 | **有持久 bash 进程** |

TS 原生 `bash` 之所以存在，是为了让 agent 能用 shell 状态（变量、export 的函数）跨多次调用累积上下文。Python 这边的 `shell` 没有这种"持久 bash"——它强制走 sandbox。

### 6.2 推荐：用 `make_shell` 替代默认 shell

```python
from strands import Agent
from strands.vended_tools import make_shell
from strands.sandbox.docker import DockerSandbox

sandbox = DockerSandbox("agent-workspace")
locked_shell = make_shell(
    sandbox=sandbox,
    name="sandbox_shell",
    description="Run read-only shell commands. Do not modify files.",  # 提示词约束
)
agent = Agent(sandbox=sandbox, tools=[locked_shell])
```

**注意命名冲突**：如果 Agent 已注册同名工具，sandbox 自带的版本会被**跳过**——允许你用更严格变体覆盖默认。

### 6.3 自定义工具访问 sandbox

通过 `@tool(context="tool_context")` 装饰的工具可访问 agent 的 sandbox：

```python
from strands.types.tools import ToolContext


@tool(context="tool_context")
async def lint(path: str, tool_context: ToolContext) -> list:
    """Lint a file and return structured errors."""
    result = await tool_context.agent.sandbox.execute(f"eslint --format json {path}")
    issues = json.loads(result.stdout)
    return [msg for file in issues for msg in file["messages"]]


agent = Agent(
    sandbox=DockerSandbox("my-dev-env"),
    tools=[lint],
)
```

**关键事实**：所有工具（sandbox 自带的 + 你写的）**共享一个 sandbox 实例**。不论谁发起执行，路由的是同一个执行环境。

### 6.4 vendored 插件与 sandbox

| 插件 | 行为 |
|------|------|
| **Agent Skills** | 从**本地路径**加载技能时，文件读通过 sandbox——容器/远程上的技能**无需复制**到宿主机 |
| **Context Offloader** | 用 `FileStorage` 时，卸载产物的 I/O 也走 sandbox |
| URL / 内联技能 | 与 sandbox 无关 |

插件在初始化时**自动绑定**到 agent 的 sandbox，无需任何额外配置。

### 6.5 Vended Tools 总览

| 工具 | 用途 | 平台 |
|------|------|------|
| `file_editor` | 查看/创建/编辑文件 | Python + TS (Node.js) |
| `shell` / `bash` | shell 命令执行 | Python 全平台 / TS Node.js |
| `http_request` | HTTP API 调用 | Python + TS Node.js 20+/browser |
| `notebook` | 持久化任务笔记 | TS only |
| `sleep` | 有界暂停，可取消 | Python + TS + browser |
| `stop`（实验性） | 显式结束 agent 循环 | Python + TS |

**所有 vended tools 都有"执行任意操作"的固有风险**——官方安全警告直接说"仅与可信输入一起使用，生产环境应在沙箱环境中运行"。

---

## 七、最佳实践清单

1. **生产环境必须传 sandbox**——不传等于把 root 权限交给 LLM。
2. **用 `make_shell` / `make_file_editor` 工厂收紧工具能力**——通过系统提示词约束只读或单目录。
3. **理解"安全来自环境，不是接口"**——容器里以非 root 跑、限制挂载、限制网络。
4. **不要轻易打开 `allow_unsafe_ssh_options`**——严格禁止在模型生成或不受信输入上启用。
5. **流式用 `execute_streaming`，非流式用 `execute`**——前者消费流拿原始 chunk，后者消费流拿到最终 `ExecutionResult`。
6. **TS 想用持久 bash**——主机上跑 `bash` 工具是合理选择；其它所有场景都应该走 sandbox。

---

## 八、本地 1.53.0 vs 官方最新版的差异

通过对比仓库已装的 `strands/sandbox/` 源码、官方文档与 `tests/demo_sandbox.py` 的实测结果：

| 项 | 本地 1.53.0 | 官方最新版 |
|----|-------------|----------|
| 抽象基类 | `Sandbox` / `PosixShellSandbox` | 同 |
| 内置后端 | `DockerSandbox` / `SshSandbox` + `NotASandboxLocalEnvironment` | 同 |
| 流式接口 | `execute_streaming` | + `execute_code_streaming`（同样的流式协议） |
| 文件操作 | `read_file/write_file/remove_file/list_files` | 同 + `read_text`/`write_text` 便捷封装 |
| 默认 fallback | `NotASandboxLocalEnvironment`（无隔离） | 同 |
| **自动注入工具名** | `shell` + `file_editor`（`vended_tools`） | `sandbox_shell` + `sandbox_file_editor` |
| vended_tools 路径 | `strands.vended_tools.shell/file_editor` | 同 + 提供 `make_shell` / `make_file_editor` 工厂 |
| `get_tools()` 默认实现 | 空 list（**不自动注入** `shell`/`file_editor`） | 返回 `sandbox_shell` + `sandbox_file_editor`（自动注入） |
| Agent 注入点 | `Agent(sandbox=...)` 存到 `_sandbox` | 同 |

### 8.1 升级时大概率遇到的坑

1. **工具名冲突**：1.53.0 用 `file_editor`，新版改名为 `sandbox_file_editor`。升级 SDK 时 `Agent(tools=[file_editor], sandbox=DockerSandbox(...))` 会变成两个同名工具。
2. **`sandbox_shell` 在 1.53.0 不存在**：本地默认走 `strands.vended_tools.shell` 这个**不带 sandbox 路由的版本**。一旦升级，你就必须显式 `tools=[make_shell(sandbox=...)]`。
3. **`get_tools()` 默认行为变化**：1.53.0 的 sandbox `get_tools()` 返回 `[]`，所以默认不自动注入任何工具；新版会自动注入 `sandbox_shell`/`sandbox_file_editor`。

### 8.2 本地 1.53.0 推荐用法（保持向后兼容）

```python
# 不依赖 sandbox 路由（默认）
from strands import Agent
from strands.vended_tools import file_editor, shell

agent = Agent(tools=[shell, file_editor])

# 想用 sandbox
from strands.sandbox.docker import DockerSandbox
from strands.vended_tools.file_editor import make_file_editor

sandbox = DockerSandbox(container="...")
agent = Agent(
    sandbox=sandbox,
    tools=[
        make_file_editor(sandbox=sandbox),  # ✅ 绑定到 sandbox
        # 不要加 vended_tools.shell——它是不带 sandbox 路由的版本
    ],
)
```

### 8.3 本地扩展的实战 demo

`tests/demo_sandbox.py` 把官方文档的关键模式做成 8 种可一键复现：

| Pattern | 对应官方概念 |
|---------|------------|
| 1. 类层级探索 | `Sandbox` / `PosixShellSandbox` 抽象方法 |
| 2. 默认行为 | `NotASandboxLocalEnvironment` |
| 3. 自定义 PosixShellSandbox | 第五章「推荐路径」 |
| 4. file_editor 路由 | `make_file_editor(sandbox=...)` 工厂 |
| 5. 不同 sandbox 构造 | `DockerSandbox` / `SshSandbox` 参数对比 |
| 6. sandbox 注入 Agent | `Agent(sandbox=...)` |
| **7. execute_code_streaming** | 官方新接口，1.53.0 已支持 |
| **8. 文件 I/O 抽象方法** | `read_file/write_file/list_files/remove_file`（1.53.0 内置 `PosixShellSandbox` 默认实现） |

Pattern 7 和 8 是本次新增，覆盖了官方文档中的两个未在原有 demo 里出现的关键能力：

- **`execute_code_streaming`**：用 base64 + heredoc 把代码送到目标语言解释器，避免 shell 注入。
- **文件 I/O 抽象方法**：`PosixShellSandbox` 默认通过 `base64` / `ls -1ap` / `rm` / `mkdir -p` 实现 4 个方法——子类只需实现 `execute_streaming` 即可"白嫖"。

### 8.4 Windows 实测的特殊处理

Pattern 7/8 通过 bash 跑 POSIX shell，但**Windows 默认 shell 是 cmd.exe**（没有 heredoc、没有 base64）。demo 加了两个跨平台补丁：

```python
def _to_bash_path(path: str) -> str:
    """C:\\Users\\foo → /mnt/c/Users/foo（WSL bash 路径）"""


def _run_bash_via_script(command: str, *, cwd=None, env=None):
    """把多行 shell 写到 .sh 临时文件，bash <file> 执行
    （避免 bash -c "heredoc" 被 quoting 破坏）"""
```

实测在 WSL 环境下两个 pattern 都跑通真实 shell：pattern 7 真的把 Python 代码通过 heredoc 喂给 WSL 的 `/usr/bin/python3`，pattern 8 真的在 `/mnt/c/...` 下创建、读取、列出、删除文件并校验二进制 round-trip。

---

## 九、参考链接

- 官方概览：<https://strandsagents.com/docs/user-guide/concepts/sandbox/>
- 可用 Sandbox：<https://strandsagents.com/docs/user-guide/concepts/sandbox/available-sandboxes/>
- 自定义 Sandbox：<https://strandsagents.com/docs/user-guide/concepts/sandbox/custom-sandbox/>
- Vended Tools：<https://strandsagents.com/docs/user-guide/concepts/tools/vended-tools/>
- 本地源码：
  - `strands/sandbox/base.py` —— 抽象基类
  - `strands/sandbox/posix_shell.py` —— shell 后端基类（含文件 I/O 默认实现）
  - `strands/sandbox/docker.py`
  - `strands/sandbox/ssh.py`
  - `strands/sandbox/not_a_sandbox_local_environment.py`
  - `strands/sandbox/types.py` —— `StreamChunk` / `ExecutionResult` / `FileInfo` / `OutputFile` / `StreamType`
- 本地实战：`tests/demo_sandbox.py --all`（**8/8 通过**）
- 本地旧版笔记：`docs/Sandbox体系实战.md`（基于 1.53.0 接口）
