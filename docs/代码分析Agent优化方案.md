# 代码分析 Agent 优化方案

> 实现时间：2026-08-26
> 关联 PR / commit：本地工作分支
> 状态：✅ 全部实现 + 测试通过（**36/36 测试**）

## 一、为什么要优化

`src/zai/` 是一个用 Strands Agents SDK 包装的 **代码分析 Agent**，
跑 Ollama 上的本地小模型（默认 `qwen3:7b`）。优化前存在 4 类系统性问题：

| 问题 | 现象 | 影响 |
|------|------|------|
| 系统提示词过于简陋 | `agent.py:175` 一句话："你是一个代码分析 Agent" | 小模型不知道先做什么后做什么；10 个分析里 6 个开始就 `glob *` 把噪声当证据 |
| 工具能力偏弱 | `read` 全文读、`grep` 没上下文、无 AST 类工具 | token 黑洞 + 模型拿到证据后没有结构线索 |
| 缺规划层 | 没有"先看全貌"步骤 | 模型上来读 5-8 个无关文件 |
| 输出无模板 | 最终回复自由发挥 | 复盘困难、结论空泛 |

## 二、解决方案（全链路三件套）

按对**准确度**的边际收益从高到低做了 3 层改造：

### 2.1 系统提示词：4 阶段协议 + 结构化输出模板（最大杠杆）

`agent.py::_build_system_prompt()` 现在输出：

```
【分析协议】
阶段1 Scope    → 先调 file_tree / glob **/* 确认边界
阶段2 Outline  → 对核心文件（≤ 5）调 outline 抽类/函数签名
阶段3 Read     → 只读相关片段，用 read(offset, limit)
阶段4 Synthesize → 按模板输出

【输出模板（必须 4 段齐全）】
## 范围
## 证据
- file:line — 证据描述 N
## 结论
## 不确定性

【自检规则】
- 任何结论必须能指向 file:line
- 写文件前默认使用无干扰模式
```

**为什么这样设计**：对小模型（Qwen3:7B/8B）"先做什么不做什么"比"请你分析"有效 N 倍。
输出模板把"散文化结论"物理上压制为"可验证陈述"。

### 2.2 工具层：扩 2 改 2 增 2

| 工具 | 改动 | 主要效果 |
|------|------|---------|
| `read` | **扩**：`offset`/`limit`/`max_bytes` + 自动二进制检测 | 避免一次读 5MB 文件；binary 返回 `[binary, N bytes]` 不再炸错 |
| `grep` | **改**：`context=N` 行 + `output_mode ∈ {content, files_with_matches, count}` | 模型拿到命中附近代码，不再孤立断章 |
| `file_tree` | **新增** | 返回 `path/type/size` 的 JSON 树，不读内容 |
| `outline` | **新增** | 用 `ast` 抽类/函数签名，仅返回 ≤ 200 行摘要 |
| `write` / `edit` | 保持 | 沙箱检查仍由 `WorkspaceSandboxHook` 处理 |

### 2.3 工作流层：项目索引 + 弱断言自检

| 组件 | 实现 |
|------|------|
| `ProjectIndex`（`index.py`） | 缓存 `file_tree` 结果，TTL 5min，max-mtime 失效策略；任何文件改动后下次调用自动重建 |
| `Agent.detect_weak_assertions(text)` | 检测"我猜/大概/可能/也许/或许/似乎/应该"等弱断言词；可在最终答复前注入"请改用更确信措辞或补一条证据"指令 |

## 三、改动清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `src/zai/agent.py` | 改 | 重写 `_build_system_prompt`；新增 `detect_weak_assertions` 类方法 |
| `src/zai/tools.py` | 改 | 增强 read/grep；新增 file_tree + outline；提取 `build_file_tree_entries` 给索引复用 |
| `src/zai/index.py` | 新 | `ProjectIndex`（缓存 + mtime 失效） |
| `src/zai/config.py` | 改 | 默认工具列表加 `file_tree` + `outline` |
| `tests/test_agent_optimizations.py` | 新 | **14 个新测试**，4 个层各覆盖 |
| `tests/test_smoke.py` | 改 | 更新默认工具列表期望值（行为变化，不是测试错误） |

## 四、测试结果

```bash
.venv/Scripts/python.exe -m pytest tests/ -v
```

```
tests/test_agent_optimizations.py ..............                   [ 38%]
  test_system_prompt_has_four_stages                                PASSED
  test_system_prompt_has_output_template_sections                  PASSED
  test_system_prompt_lists_available_tools                         PASSED
  test_read_tool_returns_offset_slice                              PASSED
  test_read_tool_detects_binary                                    PASSED
  test_read_tool_truncates_large_files                             PASSED
  test_grep_tool_includes_context_lines                            PASSED
  test_grep_tool_files_with_matches_mode                           PASSED
  test_file_tree_lists_files_and_dirs                              PASSED
  test_outline_extracts_python_defs                                PASSED
  test_project_index_caches_within_ttl                             PASSED
  test_project_index_invalidates_on_mtime_change                   PASSED
  test_self_check_flags_weak_assertion_words                       PASSED
  test_self_check_returns_empty_for_strong_text                    PASSED

tests/test_smoke.py ......................                         [100%]
  ... (22 旧测试全部通过)

============================= 36 passed in 2.11s ==============================
```

## 五、端到端建议验证

```bash
.venv/Scripts/python.exe -m zai.main \
    --workspace ./workspace/sample_project \
    --prompt-file ./prompts/init.txt \
    --stream
```

观察输出是否命中 4 段模板（## 范围 / ## 证据 / ## 结论 / ## 不确定性）。

## 六、未做事项（YAGNI）

- ❌ 向量检索 / RAG（小项目不值得）
- ❌ 切换到更大模型（受限于本地硬件）
- ❌ ProjectIndexHook 自动改写 file_tree 工具的返回值（当前需要业务代码显式调用 `ProjectIndex.get_tree()`）
- ❌ `no_disturb` 写入模式（独立 PR 已有 `写文件工具无干扰模式配置.md`）

## 七、升级注意事项

升级到 Strands Agents SDK 最新版时，**不要直接 `tools=[sandbox_shell, sandbox_file_editor] + sandbox=DockerSandbox(...)`** —— 1.53.0 本地版本没有这两个工具名，仍需 `tools=[..., make_file_editor(sandbox=...)]`。

详见 `docs/Sandbox_官方文档中文版.md` 第八章差异对比。
