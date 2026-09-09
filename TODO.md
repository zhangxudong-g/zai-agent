# Zai Agent - TODO

Track planned improvements and optimizations for future releases.

Format follows the [Keep a Changelog](https://keepachangelog.com/) style.

---

## 🔴 [0.4.0] - Medium Priority

### 流式输出增强

- [ ] Markdown 实时渲染（rich.live）
- [ ] 表格自动对齐
- [ ] 代码块语法高亮（stream 模式）

### Agent 监控

- [ ] `--stats` 显示使用统计
  - 会话数、总时长、Token 消耗
  - 最常调用工具、平均响应时间
- [ ] 每次会话结束显示摘要

### 缓存层

- [ ] `~/.zai/cache/file_hashes.json` 避免重复 read
- [ ] 文件修改时间跟踪
- [ ] Token 节省统计

### 错误恢复

- [x] Ollama 离线检测 + 友好提示
- [x] 网络断开自动重连
- [ ] 大响应自动分页

---

## 🟢 [0.5.0] - Future Enhancements

### MCP 集成

- [ ] Model Context Protocol 支持
- [ ] 外部工具服务器发现
- [ ] 标准 MCP 工具加载

### 插件系统

- [ ] `zai plugin install <name>` 安装插件
- [ ] `zai plugin list` 列出已安装
- [ ] 插件自动发现（`~/.zai/plugins/`）

### 性能优化

- [ ] 并行工具调用
- [ ] 流式响应优化
- [ ] Token 消耗监控

### CI 集成

- [ ] GitHub Action `zai-agent/zai-action`
- [ ] PR 自动审查
- [ ] Dockerfile 多阶段构建优化

---

## 💡 Ideas (Backlog)

这些是有待评估的想法，可能在未来的某个版本中实现：

### 远程协作

- 共享会话 URL
- 实时协作编辑
- WebSocket 同步

### Web UI

- 浏览器版本（适合远程服务器）
- WebSocket 流式输出
- 会话历史管理界面

### 多语言支持

- i18n 框架
- 英文/中文/日文支持

### 其他工具

- `web_search` - 网页内容抓取
- `code_search` - 语义代码搜索
- `pdf` - PDF 文本提取
- `image` - 图片 OCR/分析

### Agent 框架扩展

- 多 Agent 协作
- 子 Agent 委派
- 长任务分解

### 安全增强

- 工具调用审计日志
- 危险命令二次确认
- 敏感文件保护（.env, .key 等）

---

## 📊 Completed

### ✅ [0.3.0]

#### 会话管理
- [x] `/sessions` 命令列出所有历史会话
- [x] `/save <name>` 保存当前会话
- [x] `/load <name>` 恢复历史会话
- [x] `/export <name>` 导出会话为 JSON

#### 单次模型选择
- [x] `--model` 参数支持单次问答使用不同模型
- [x] `--max-tokens` 参数控制单次 token 预算

#### 项目级上下文
- [x] 项目目录下的 `.zai/context.md` 自动加载
- [x] `.zai/rules.md` 项目特定规则
- [x] `.zai/ignore` 类似 `.gitignore` 的文件排除（glob 工具支持）

### ✅ [0.2.0]

- [x] 工具增强（read/edit/shell）
- [x] 新增 diff 工具
- [x] TUI 模块（颜色、spinner、box）
- [x] REPL 模块（历史、快捷键、错误处理）
- [x] 配置系统（~/.zai/ 优先）
- [x] 卸载功能（zai -uninstall）
- [x] 117 个测试全部通过
- [x] README + CHANGELOG 更新

### ✅ [0.1.0]

- [x] 基础 Agent + 8 个工具
- [x] 双层沙箱
- [x] 流式输出

---

## 📝 Notes

- 优先级评估基于：**用户价值** × **实现难度⁻¹**
- 每个版本的目标：保持向后兼容，渐进式增强
- 所有功能都需配套测试（目标 100% 覆盖率）
- 重大变更需更新 CHANGELOG 和 README
