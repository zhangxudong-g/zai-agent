# Changelog

所有重要的项目变更都会记录在此文件中。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

## [Unreleased]

### Added
- 初始版本发布

## [0.1.0] - 2025-01-01

### Added
- 基于 Strands Agents SDK 的核心 Agent 实现
- Ollama 本地模型支持
- 交互式 REPL 模式
- 流式输出显示
- 工具集:
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
