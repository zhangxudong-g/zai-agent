# Zai Agent 0.3.0 人工测试手顺

> **目的**：人工验证 0.3.0 全部功能
> **版本**：v0.3.0
> **预计时间**：60-90 分钟

---

## 📋 测试前准备

### 1. 环境检查

```bash
# 确认 Ollama 运行中
curl http://localhost:11434/api/tags

# 确认 zai-agent 已安装
zai --version

# 查看配置
zai --info
```

### 2. 创建测试工作区

```bash
# Windows
mkdir C:\test-zai-workspace
cd C:\test-zai-workspace

# Git Bash / Linux
mkdir -p /tmp/test-zai-workspace
cd /tmp/test-zai-workspace
```

---

## 🧪 测试 1: 基础启动和帮助

### 1.1 启动 REPL

```bash
zai --workspace ./test-zai-workspace
```

**预期**：
- ✅ 显示 banner 包含 zai 标识
- ✅ 显示工作目录路径
- ✅ 显示 session id
- ✅ 进入 `> ` 提示符

### 1.2 测试 `/help`

```
> /help
```

**预期**：
- ✅ 显示帮助信息
- ✅ 包含所有命令: `/help /sessions /save /load /export /clear /info /exit`

### 1.3 测试 `/info`

```
> /info
```

**预期**：
- ✅ 显示配置目录
- ✅ 显示 sessions 目录
- ✅ 显示 saves 目录
- ✅ 显示 exports 目录

---

## 🧪 测试 2: 会话管理（0.3.0 核心）

### 2.1 测试 `/sessions`（空列表）

```
> /sessions
```

**预期**：
- ✅ 显示 "暂无保存的会话"

### 2.2 准备一个对话

```
> 给我讲一个关于编程的笑话
```

**等待回复完成**。

### 2.3 测试 `/save`

```
> /save test-session-1
```

**预期**：
- ✅ 显示 "会话已保存"
- ✅ 显示保存路径
- ✅ 检查文件存在：`ls ~/.zai/saves/test-session-1.json`

### 2.4 再次对话并保存

```
> 列出 Python 的主要特点
```

**等待回复完成**。

```
> /save test-session-2
```

**预期**：
- ✅ 第二个会话保存成功

### 2.5 测试 `/sessions`（列表）

```
> /sessions
```

**预期**：
- ✅ 显示两个会话表格
- ✅ 包含 `test-session-1` 和 `test-session-2`
- ✅ 显示消息数和创建时间

### 2.6 测试 `/export`

```
> /export backup-session
```

**预期**：
- ✅ 显示 "会话已导出"
- ✅ 检查文件存在：`ls ~/.zai/exports/backup-session.json`

### 2.7 测试 `/load`

```
> /load test-session-1
```

**预期**：
- ✅ 显示 "会话已加载"
- ✅ 显示消息数
- ✅ 显示提示 "会话上下文已恢复"

### 2.8 测试加载不存在的会话

```
> /load nonexistent-session
```

**预期**：
- ✅ 显示 "会话不存在: nonexistent-session"

### 2.9 测试 `/sessions` 命令在 help 中

```
> /help
```

**预期**：
- ✅ `/sessions` 命令在帮助列表中

### 2.10 退出 REPL

```
> /exit
```

**预期**：
- ✅ 正常退出
- ✅ 显示 "Bye!"

---

## 🧪 测试 3: 单次模型选择（0.3.0 核心）

### 3.1 测试 `--model` 参数

```bash
zai "Hello" --workspace ./test-zai-workspace --model qwen3:1.7b
```

**预期**：
- ✅ Banner 中显示 `qwen3:1.7b`
- ✅ 正常工作

### 3.2 测试 `--max-tokens` 参数

```bash
zai "List 3 colors" --workspace ./test-zai-workspace --max-tokens 100
```

**预期**：
- ✅ 正常输出
- ✅ 输出被限制在 ~100 tokens 内（响应较短）

### 3.3 测试组合参数

```bash
zai "Say hi" --workspace ./test-zai-workspace --model qwen3:1.7b --max-tokens 50 --sync
```

**预期**：
- ✅ 同步模式（无流式）
- ✅ 输出短小

---

## 🧪 测试 4: 项目级上下文（0.3.0 核心）

### 4.1 准备项目上下文

```bash
# 创建 .zai 目录
mkdir -p ./test-zai-workspace/.zai

# 创建 context.md
cat > ./test-zai-workspace/.zai/context.md << 'EOF'
# Test Project Context

This is a test Python project for a simple web API.
- Framework: FastAPI
- Database: PostgreSQL
- Testing: pytest
EOF

# 创建 rules.md
cat > ./test-zai-workspace/.zai/rules.md << 'EOF'
# Project Rules

- All code must have type hints
- Use snake_case for variables
- Write docstrings for public functions
EOF

# 创建 ignore
cat > ./test-zai-workspace/.zai/ignore << 'EOF'
# Ignored files
*.pyc
__pycache__/
*.log
EOF
```

### 4.2 验证上下文加载

```bash
zai --workspace ./test-zai-workspace
```

```
> What framework should I use for this project?
```

**预期**：
- ✅ 模型提到 FastAPI（在 context.md 中定义）
- ✅ 模型提到 pytest

### 4.3 测试 ignore 规则

```bash
# 在工作区创建一些文件
mkdir -p ./test-zai-workspace/src
mkdir -p ./test-zai-workspace/__pycache__
echo "print('main')" > ./test-zai-workspace/src/main.py
echo "cached" > ./test-zai-workspace/__pycache__/test.pyc
```

```
> List all Python files in this project
```

**预期**：
- ✅ 列出 `src/main.py`
- ✅ **不**列出 `__pycache__/test.pyc`（被 ignore 排除）

### 4.4 删除 .zai 目录验证优雅降级

```bash
mv ./test-zai-workspace/.zai ./test-zai-workspace/.zai.backup
zai --workspace ./test-zai-workspace
```

```
> What framework should I use?
```

**预期**：
- ✅ 正常响应（不报错）
- ✅ 不包含项目特定信息

```bash
# 恢复
mv ./test-zai-workspace/.zai.backup ./test-zai-workspace/.zai
```

---

## 🧪 测试 5: 安全干预（基于 Strands InterventionHandler）

### 5.1 测试危险命令二次确认

```bash
zai --workspace ./test-zai-workspace
```

```
> Delete all log files in the current directory using rm
```

**预期**：
- ✅ 模型提出 `rm *.log` 或类似命令
- ✅ 触发 **Confirm**（要求确认）
- ✅ 模型收到警告

**或者手动触发**：

```
> Run this command: rm -rf /tmp/test-dangerous
```

**预期**：
- ✅ 触发 `DangerousCommandIntervention`
- ✅ 显示确认提示

### 5.2 测试完全阻断的危险命令

```
> Run: shutdown -h now
```

**预期**：
- ✅ 触发 **Deny**（自动阻断）
- ✅ 模型收到 "Blocked: system power control"

### 5.3 测试敏感文件保护

```
> Read the file .env
```

**预期**：
- ✅ 触发 **Deny**（自动阻断）
- ✅ 显示 "🔒 Blocked access to sensitive file: .env"

### 5.4 测试写入敏感文件

```
> Write 'SECRET=test' to .env
```

**预期**：
- ✅ 触发 **Deny**（自动阻断写入）
- ✅ 显示 "🔒 Blocked write to sensitive file: .env"

### 5.5 测试 SSH 密钥保护

```
> Read ~/.ssh/id_rsa
```

**预期**：
- ✅ 触发 **Deny**
- ✅ 显示文件保护消息

### 5.6 测试禁用干预

```bash
zai --workspace ./test-zai-workspace --no-interventions
```

```
> Read the file .env
```

**预期**：
- ✅ **不会**触发阻断
- ✅ 模型可以读取 .env（如果存在）

### 5.7 退出 REPL

```
> /exit
```

---

## 🧪 测试 6: 技能插件系统（基于 Strands AgentSkills）

### 6.1 创建测试技能

```bash
# 创建技能目录
mkdir -p ~/.zai/plugins/skills/code-review

# 创建 SKILL.md
cat > ~/.zai/plugins/skills/code-review/SKILL.md << 'EOF'
---
name: code-review
description: A skill for performing code reviews with focus on quality and best practices
---

# Code Review Skill

When activated, you should:

1. Check for type hints
2. Look for proper error handling
3. Verify code style follows PEP 8
4. Suggest improvements
EOF
```

### 6.2 创建第二个技能

```bash
mkdir -p ~/.zai/plugins/skills/test-writer

cat > ~/.zai/plugins/skills/test-writer/SKILL.md << 'EOF'
---
name: test-writer
description: A skill for writing comprehensive pytest unit tests
---

# Test Writer Skill

When activated, you should:

1. Write pytest-style unit tests
2. Cover edge cases
3. Use mocks appropriately
4. Follow AAA pattern (Arrange, Act, Assert)
EOF
```

### 6.3 启用技能插件

```bash
zai --workspace ./test-zai-workspace --skills
```

**预期**：
- ✅ REPL 正常启动
- ✅ 技能列表被注入到系统提示

### 6.4 测试技能激活

```
> I want to review my code for quality
```

**预期**：
- ✅ 模型识别出应该激活 `code-review` 技能
- ✅ 模型开始按技能指令执行

### 6.5 测试另一个技能

```
> Help me write unit tests
```

**预期**：
- ✅ 模型识别出 `test-writer` 技能
- ✅ 按技能指令生成测试

### 6.6 测试技能目录

```bash
ls ~/.zai/plugins/skills/
```

**预期**：
- ✅ 包含 `code-review/` 和 `test-writer/` 两个目录

### 6.7 测试无技能模式

```bash
zai --workspace ./test-zai-workspace
```

```
> I want to review my code for quality
```

**预期**：
- ✅ 模型仍能正常回复（但可能不会激活特定技能）

---

## 🧪 测试 7: 并行工具执行

### 7.1 测试串行模式（默认）

```bash
zai --workspace ./test-zai-workspace
```

```
> List all Python files, then read the README.md, then show file tree
```

**预期**：
- ✅ 工具按顺序执行
- ✅ 输出按调用顺序

### 7.2 测试并行模式

```bash
zai --workspace ./test-zai-workspace --concurrent-tools
```

```
> Read README.md, list Python files, and show file tree all at once
```

**预期**：
- ✅ 工具并发执行
- ✅ 输出更快（如果模型选择并发调用）

### 7.3 对比测试

**串行**：

```bash
time zai --workspace ./test-zai-workspace --sync "Read README.md and list *.py files"
```

**并行**：

```bash
time zai --workspace ./test-zai-workspace --sync --concurrent-tools "Read README.md and list *.py files"
```

**预期**：
- ✅ 并行模式通常更快（如果多次工具调用）
- ✅ 但结果应一致

---

## 🧪 测试 8: 错误恢复（已实现功能）

### 8.1 测试 Ollama 离线检测

```bash
# 停止 Ollama（如果可能）
# 或使用错误的 URL
zai "Hello" --workspace ./test-zai-workspace
```

**预期**（如果 Ollama 离线）：
- ✅ 显示友好的错误信息
- ✅ 包含排查提示：
  - 检查 Ollama 是否运行
  - 检查 OLLAMA_BASE_URL
  - 检查模型是否下载

### 8.2 测试网络重连

```bash
zai "Hello" --workspace ./test-zai-workspace --max-retries 5
```

**预期**：
- ✅ 如果连接失败，自动重试
- ✅ 显示重试消息：`[Retry X/5]`

---

## 🧪 测试 9: 完整工作流

### 9.1 综合场景

```bash
# 启动带所有功能的会话
zai --workspace ./test-zai-workspace --skills --concurrent-tools
```

**测试流程**：

1. **查询项目信息**
```
> What is this project about?
```
**预期**：模型读取 `.zai/context.md`，提到 FastAPI 等

2. **代码搜索**
```
> Find all Python files
```
**预期**：列出 `src/main.py`，不列出 `__pycache__/` 下的文件

3. **保存进度**
```
> /save work-progress
```
**预期**：成功保存

4. **危险命令测试**
```
> Delete all .pyc files in __pycache__
```
**预期**：触发 Confirm 或 Deny（取决于具体命令）

5. **继续工作**
```
> Read src/main.py
```
**预期**：正常读取

6. **导出完整会话**
```
> /export final-report
```
**预期**：导出成功到 `~/.zai/exports/final-report.json`

7. **列出所有会话**
```
> /sessions
```
**预期**：显示所有保存的会话

8. **退出**
```
> /exit
```

---

## 🧪 测试 10: 边界情况

### 10.1 空提示符

```
> 
```
（直接按回车）

**预期**：
- ✅ 不报错
- ✅ 继续等待输入

### 10.2 无效命令

```
> /invalidcommand
```

**预期**：
- ✅ 不识别为命令
- ✅ 尝试作为提示词发送给模型

### 10.3 加载不存在的会话

```
> /load does-not-exist
```

**预期**：
- ✅ 显示 "会话不存在: does-not-exist"

### 10.4 导出空会话

```bash
zai --workspace ./test-zai-workspace
```
```
> /export empty-session
```

**预期**：
- ✅ 导出成功（即使没有消息）

### 10.5 超长提示词

```
> [粘贴非常长的代码块...]
```

**预期**：
- ✅ 正常工作
- ✅ 可能受 `--max-tokens` 限制

---

## 📊 测试结果记录

| 测试项 | 通过 | 失败 | 备注 |
|--------|------|------|------|
| 1. 基础启动 | ☐ | ☐ | |
| 2. 会话管理 | ☐ | ☐ | |
| 3. 模型选择 | ☐ | ☐ | |
| 4. 项目上下文 | ☐ | ☐ | |
| 5. 安全干预 | ☐ | ☐ | |
| 6. 技能插件 | ☐ | ☐ | |
| 7. 并行工具 | ☐ | ☐ | |
| 8. 错误恢复 | ☐ | ☐ | |
| 9. 完整工作流 | ☐ | ☐ | |
| 10. 边界情况 | ☐ | ☐ | |

---

## 🐛 问题报告模板

发现问题时，请记录：

```markdown
### 问题描述
[简述问题]

### 复现步骤
1. ...
2. ...

### 预期结果
[应该发生什么]

### 实际结果
[实际发生了什么]

### 环境信息
- 操作系统: [Windows/Linux/macOS]
- Python 版本: [3.12/3.13/...]
- Ollama 版本: [...]
- 模型: [...]

### 日志
[粘贴相关错误信息]
```

---

## ✅ 快速检查清单

启动测试前确认：

- [ ] Ollama 服务运行中
- [ ] `zai --help` 显示所有新参数
- [ ] 测试工作区已创建
- [ ] `.zai` 目录文件已准备

完成测试后确认：

- [ ] 所有 154+ 个自动化测试通过
- [ ] 人工测试无重大问题
- [ ] 文档（CHANGELOG/TODO）已更新
- [ ] 提交记录完整

---

**祝测试顺利！** 🎉
