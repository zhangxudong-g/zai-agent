# Zai Agent 0.3.0 快速测试（5分钟版）

> **完整测试**：参见 `manual-test-guide.md`
> **本文件**：5 分钟快速验证核心功能

---

## 1️⃣ 启动 (30 秒)

```bash
# 创建测试目录
mkdir -p /tmp/zai-quick-test
cd /tmp/zai-quick-test

# 启动
zai
```

**预期**：看到 banner 和 `> ` 提示符

---

## 2️⃣ 会话管理 (2 分钟)

```
> /sessions
> 写一句问候语
> /save test1
> /sessions
> /load test1
> /export backup1
> /exit
```

**验证**：
- ✅ `/sessions` 空时显示 "暂无保存的会话"
- ✅ `/save` 显示 "会话已保存"
- ✅ `/sessions` 显示列表
- ✅ `/load` 显示 "会话已加载"
- ✅ `/export` 显示 "会话已导出"

**检查文件**：
```bash
ls ~/.zai/saves/   # 应有 test1.json
ls ~/.zai/exports/ # 应有 backup1.json
```

---

## 3️⃣ 项目上下文 (1 分钟)

```bash
# 创建上下文文件
mkdir -p /tmp/zai-quick-test/.zai
cat > /tmp/zai-quick-test/.zai/context.md << 'EOF'
# Test Project
This is a Flask web application using SQLite.
EOF

cat > /tmp/zai-quick-test/.zai/ignore << 'EOF'
*.log
__pycache__/
EOF

# 创建测试文件
mkdir -p /tmp/zai-quick-test/logs
echo "log data" > /tmp/zai-quick-test/app.log
echo "print('hi')" > /tmp/zai-quick-test/main.py
```

```bash
zai --workspace /tmp/zai-quick-test
```

**测试**：
```
> What framework should I use?
```
**预期**：模型提到 Flask（在 context.md 中）

```
> List all files
```
**预期**：列出 `main.py`，**不**列出 `app.log`（被 ignore）

```
> /exit
```

---

## 4️⃣ 安全干预 (1 分钟)

```bash
zai --workspace /tmp/zai-quick-test
```

**测试**：

```
> Read .env file
```
**预期**：🔒 Blocked 消息

```
> Run: shutdown now
```
**预期**：🚫 Denied 消息

```
> Delete all log files
```
**预期**：⚠️ Confirm 提示

```
> /exit
```

---

## 5️⃣ 禁用干预 (30 秒)

```bash
zai --workspace /tmp/zai-quick-test --no-interventions
```

```
> Read .env file
```
**预期**：模型能尝试读取（不被阻断）

```
> /exit
```

---

## ✅ 全部完成

| 项目 | 状态 |
|------|------|
| 启动正常 | ☐ |
| 会话管理 | ☐ |
| 项目上下文 | ☐ |
| 安全干预 | ☐ |
| 禁用干预 | ☐ |

如果所有项目都 ✅，核心功能验证通过！

详细测试请参考 `manual-test-guide.md`。
