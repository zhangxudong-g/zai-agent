# Contributing to Zai Agent

感谢您对 Zai Agent 的关注！欢迎提交 Issue 和 Pull Request。

## 开发环境设置

```bash
# 克隆仓库
git clone https://github.com/your-org/zai-agents.git
cd zai-agents

# 安装依赖
uv sync

# 安装开发依赖
uv sync --group dev

# 运行测试
uv run pytest -v
```

## 代码规范

- 使用 `uv run ruff check .` 检查代码风格
- 使用 `uv run ruff format .` 格式化代码
- 所有新功能必须有对应的测试

## 分支管理

- `main` - 稳定版本
- `develop` - 开发分支
- 功能分支命名: `feat/xxx`, `fix/xxx`, `docs/xxx`

## 提交规范

使用 [Conventional Commits](https://www.conventionalcommits.org/)：

```
feat: add new tool for code review
fix: resolve sandbox path validation bug
docs: update README with new examples
refactor: extract LLM client logic
test: add integration tests for shell command
```

## Pull Request 流程

1. Fork 仓库并创建功能分支
2. 确保所有测试通过 `uv run pytest`
3. 确保代码通过 lint 检查 `uv run ruff check .`
4. 提交 PR 并描述变更内容
5. 等待代码 review

## 报告 Bug

请使用 [Bug Report Template](./.github/ISSUE_TEMPLATE/bug_report.md)，包含：
- 复现步骤
- 预期行为
- 实际行为
- 环境信息 (OS, Python version, Ollama version)

## 提出功能建议

请使用 [Feature Request Template](./.github/ISSUE_TEMPLATE/feature_request.md)，包含：
- 问题背景
- 期望的解决方案
- 可能的替代方案
