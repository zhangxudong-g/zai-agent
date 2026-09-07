# TUI 优化对比 — 选项 A (紧凑流式 / chat 风格)

基于你的实际截图(同一次 `最近提交了什么` REPL 会话)做前后对比。

---

## 当前输出(原文照搬)

```
╭─ Zai Agent ──────────────────────────────
│ Model:     qwen3.8:27b
│ Workspace: D:\AI_Projects
│ Session:   20260907_051723_0105
│ Mode:      REPL (连续对话)
╰────────────────────────────────────────────


╭─ Zai Agent REPL ─────────────────────────
│ /help   显示帮助
│ /clear  清屏
│ /exit   退出
╰─────────────────────────────────────────────


[1] > 最近提交了什么

  🔧 shell(command='git log --oneline -15')

  🔧 shell(command="git log -5 --format='---\n%h  %an  %ar\n%s%n%b'")

  🔧 shell(command="git log -5 --pretty=format:'%h | %an | %ar | %s'")

  🔧 shell(command='git log -5 --pretty=format:"COMMIT %h AUTHOR %an TIME %ar MS...')
最近的提交如下(按时间倒序):

| 提交 | 作者 | 时间 | 说明 |
|------|------|------|------|
| `6cade60` | zhxd | 2 周前 | 添加Ollama和Qwen/GPT模型离线升级脚本和文档 |
| `892db8d` | zhangxudong-g | 10 周前 | Merge PR #4 from feature/v2-implementation |
| ... 共 11 行 ... |
| `79363f2` | — | — | feat(skills): add skill injection module for prompt prepending |

最近一次提交 `6cade60` 是 2 周前,添加了 Ollama 和 Qwen/GPT 模型的离线升级脚本和文档。

需要我查看某个具体提交改动的文件吗?
✓ (19.4s, 0 tokens)

[2] > 
📝 D:\AI_Projects\sessions\20260907_051723_0105.jsonl
```

**问题汇总:**
- 🔴 顶部连发两个 banner,11 行元信息
- 🔴 4 个 tool 行每个都重复 `shell(command=...)` 长串,被截断成 `...` 没意义
- 🟡 空行很多(prompt 前/工具间/答案前后/完成行后)
- 🟡 `✓ (19.4s, 0 tokens)` 的 `0 tokens` 误导(因为之前的 retry 问题)
- 🟡 📝 日志路径在结尾才出现,不如一开始就告诉用户

---

## 选项 A 输出(提议)

```
╭─ zai · qwen3.8:27b · D:\AI_Projects · session 20260907_051723_0105 ─────────
╰─ 📝 D:\AI_Projects\sessions\20260907_051723_0105.jsonl  ── /help /clear /exit

[1] > 最近提交了什么
  🔧 git log --oneline -15
  🔧 git log -5 --format='---\n%h  %an  %ar\n%s%n%b'
  🔧 git log -5 --pretty=format:'%h | %an | %ar | %s'
  🔧 git log -5 --pretty=format:"COMMIT %h AUTHOR %an TIME %ar MS..."

最近的提交如下(按时间倒序):

| 提交 | 作者 | 时间 | 说明 |
|------|------|------|------|
| `6cade60` | zhxd | 2 周前 | 添加Ollama和Qwen/GPT模型离线升级脚本和文档 |
| `892db8d` | zhangxudong-g | 10 周前 | Merge PR #4 from feature/v2-implementation |
| ... 共 11 行 ... |
| `79363f2` | — | — | feat(skills): add skill injection module for prompt prepending |

最近一次提交 `6cade60` 是 2 周前,添加了 Ollama 和 Qwen/GPT 模型的离线升级脚本和文档。

需要我查看某个具体提交改动的文件吗?

✓ 19.4s

[2] > 
```

---

## 改进点(逐项对照)

| # | 当前 | 选项 A | 说明 |
|---|------|--------|------|
| 1 | 两个 banner 共 ~12 行 | 单 banner 2 行 | 合并 workspace/model/session + 日志路径 + REPL 命令 |
| 2 | `🔧 shell(command='git log ...')` 全串 | `🔧 git log ...` (去 `shell(command=...)` 包装) | 不再被截断,看到的就是命令 |
| 3 | prompt 前空 1 行 | 不空 | 紧凑 |
| 4 | 工具调用之间各空 1 行 | 紧贴排列 | 一次性看到「跑了哪 4 步」 |
| 5 | 答案前后空行 | 答案前空 1 行(起视觉分段),答案后不空 | 紧凑 |
| 6 | `✓ (19.4s, 0 tokens)` | `✓ 19.4s` | 去掉误导的 0 tokens;括号多余 |
| 7 | 答案和下一 prompt 中间空 1 行 | 答案和 `✓` 之间空 1 行(分段),`✓` 后不空 | |
| 8 | 日志路径在结尾 | 在 banner 里 | 一启动就知道日志在哪 |

---

## 关键设计选择

1. **工具命令展示**: `🔧 <命令本体>`,**不再显示** `shell(command='...')` 的 dict wrapper
   - 命令太长时仍截断,但用 `…` 而不是 `...`(中文环境更顺眼)
   - 工具名仅在不是 `shell` 时显示(如 `🔧 read src/main.py` 不显示 `🔧 shell (command='read src/main.py')`)

2. **多命令一行还是多行?**: 多行
   - 紧贴排列(无空行)
   - 每个工具一行 `🔧 <cmd>`
   - 用户能直观看到 agent 跑了哪几步

3. **模型 answer 格式**: 完全不变(markdown 渲染由 LLM 输出决定)

4. **`tool_end` chunk**: 仍然 pass(由 JsonlTraceHook 写 JSONL 日志),不在 REPL 屏上重复
   - 如果日后想做「显示工具结果」,再加 `show_tool_results: bool` 参数

5. **完成行 `✓`**: 简化到 `✓ 19.4s`,不显示 tokens(tokens 显示有 bug,显示成 0 会误导)
   - 也可保留 `--verbose` 标志时显示完整 `✓ 19.4s · 432 tokens in · 87 out`

---

## 代码改动范围(预计)

只动 `src/Zai_poc/main.py`:
- `print_banner()`: 改格式,合并两 banner,加日志路径
- `_main()` 末尾的 `print(f"\\n📝 {logger.log_file}")`: 删(移到 banner)
- `_render_stream_chunks()`:
  - `tool_start` 改 print `🔧 {cmd}`,不再 `shell(command='...')`
  - `done` 改 print `✓ {elapsed:.1f}s`,去 tokens
  - 微调空白

**预计 diff**: +15 / -15 行,**纯格式化**,不改行为。

测试影响: 0(REPL 渲染层本来就没测试覆盖;现有 65 测试照过)。

---

要这样开干吗?如果还想调整某项(比如要不要保留 tokens、要不要显示工具结果),现在说。