# Changelog

所有重要的项目变更都会记录在此文件中。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

## [Unreleased]

## [0.3.0] - 2026-01-XX

### Added
- **会话管理**（`session_manager.py`, `snapshot.py`）：
  - `/sessions` 列出已保存的会话
  - `/save <name>` 保存当前会话快照
  - `/load <name>` 恢复历史会话
  - `/export <name>` 导出会话为 JSON
  - `~/.zai/saves/` 和 `~/.zai/exports/` 目录
- **模型参数**（`main.py`, `llm.py`）：
  - `--model` 参数（已有）
  - `--max-tokens` 控制单次 token 预算
- **项目上下文**（`context_loader.py`, `agent.py`）：
  - `.zai/context.md` 自动加载到系统提示
  - `.zai/rules.md` 项目特定规则
  - `.zai/ignore` 文件排除（glob 工具支持）
- **新增模块**：
  - `snapshot.py` - 会话快照序列化（保留为兼容层）
  - `session_manager.py` - 会话管理（基于 Strands 内置机制）
  - `context_loader.py` - 项目上下文加载
  - `interventions.py` - 危险命令与敏感文件保护（基于 Strands `InterventionHandler`）
  - `skills.py` - 技能插件系统（基于 Strands `AgentSkills`）
  - `executors.py` - 并行/串行工具执行器（基于 Strands `ConcurrentToolExecutor`）
- **CLI 参数**：
  - `--no-interventions` 禁用安全干预
  - `--skills` 启用技能插件
  - `--concurrent-tools` 启用并行工具执行

### Changed
- **重构**: `session_manager.py` 重写以使用 Strands SDK 内置的 `SnapshotSessionManager` 和 `LocalFileStorage` 作为底层存储
- **重构**: Agent 集成 Strands 内置的 `interventions`、`plugins`、`tool_executor` 机制
- 版本号更新为 0.3.0
- REPL 帮助信息更新（新增会话命令）
- Agent 系统提示增强（可选项目上下文）

## [0.2.0] - 2026-01-08

### Added
- **工具增强**：
  - `read`：自动编码检测（UTF-8/GBK/UTF-16 via charset-normalizer）
  - `read`：1MB 大文件自动保护
  - `edit`：多次匹配时报错（不再静默替换）
  - `edit`：`replace_all=True` 显式替换全部
  - `edit`：`dry_run=True` 仅预览不写入
  - `edit`：原子写入（tmp + rename，防止崩溃损坏）
  - `shell`：stderr 单独返回，exit code 显示
  - `shell`：可配置 timeout 和 max_output
  - `diff`：🆕 新增预览修改的工具
- **TUI 模块**（`tui.py`）：
  - ANSI 颜色（红/绿/黄/蓝/青/品红/灰/粗体）
  - `Spinner` 类用于长操作
  - `print_box` 带边框面板
  - `print_banner_v2` 改进的 banner
  - Markdown 渲染（可选 Rich）
- **REPL 模块**（`repl.py`）：
  - 基于 prompt_toolkit 的增强 REPL
  - 持久化历史（`~/.zai/history`）
  - ↑/↓ 浏览历史、Ctrl+R 搜索
  - Ctrl+C 智能取消（运行时取消，空闲时退出）
  - Ctrl+D EOF、Ctrl+L 清屏
  - 友好的错误信息（带排查提示）
- **配置系统**：
  - 统一 `~/.zai/` 数据目录
  - `~/.zai/config/.env` 优先加载
  - `init_zai_config()` 自动创建默认配置
  - `ZAI_HOME` 环境变量自定义目录
- **卸载功能**：
  - `zai -uninstall` 一键清理 pip 包 + `~/.zai/`
  - 临时脚本方式避免自我删除问题

### Changed
- `dependencies` 重组：增加 `prompt-toolkit`、`charset-normalizer`
- 配置加载顺序：`~/.zai/config/.env` > `$ZAI_CONFIG` > 当前目录 `.env`
- 测试从 42 个增加到 117 个
- Lint/Format 严格化（ruff）

### Fixed
- `file_tree` 处理 lone surrogates（之前会 UnicodeEncodeError）
- `edit` 多次匹配不再静默替换
- 卸载时不再因为 zai.exe 被删除而中断

## [0.1.0] - 2025-01-01

### Added
- 基于 Strands Agents SDK 的核心 Agent 实现
- Ollama 本地模型支持
- 交互式 REPL 模式
- 流式输出显示
- 工具集：
  - `read` - 读取文件内容
  - `glob` - 文件路径搜索
  - `grep` - 内容正则搜索
  - `file_tree` - 目录树结构
  - `outline` - Python 代码大纲
  - `shell` - 执行 shell 命令
  - `write` - 写入文件
  - `edit` - 编辑文件
- 两层沙箱安全机制
- 自动重试机制
- 会话日志记录
- CLI 参数支持