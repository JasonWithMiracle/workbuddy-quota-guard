---
name: workbuddy-quota-guard
description: "WorkBuddy/CodeBuddy 配额感知器：反推各模型 429 限额、估算当前剩余额度、生成断点交接文档与可视化看板。Use when user mentions \"还剩多少额度\", \"配额\", \"限额\", \"429\", \"限流\", \"用完额度\", \"quota\", \"rate limit\", \"还够用吗\", \"换个模型\", \"任务被中断\", \"生成交接文档\", \"handoff\"."
display_name: "配额感知器"
description_zh: "WorkBuddy/CodeBuddy 配额感知器：反推各模型 429 限额、估算当前剩余额度、生成断点交接文档与可视化看板。"
description_en: "WorkBuddy/CodeBuddy quota guard: reverse-engineer per-model 429 limits, estimate remaining quota, generate handoff docs and dashboards."
agent_created: true
type: tool
version: "1.0.0"
license: MIT
---

# WorkBuddy 配额感知器

> 在 429 把任务打断之前，先回答：**这个模型还剩多少额度？**

## 职责边界

**本 Skill 做什么**

- 只读扫描 `~/.workbuddy/projects/**/*.jsonl` 与 `~/.workbuddy/workbuddy.db`
- 反推「账号 × 模型」独立限额（滚动 24h 窗口口径）
- 输出当前各模型的用量占比与风险等级（low / medium / high）
- 生成 HTML 看板与 429 断点交接文档
- 供 Hook 在会话开始时做自动预警

**本 Skill 不做什么**

- ❌ 不修改、不删除任何 WorkBuddy 本地数据
- ❌ 不上传任何数据到网络
- ❌ 不声称能拿到**真实**剩余额度（客户端令牌存于 Electron DPAPI 安全存储，外部脚本读不到；本 Skill 只能从日志反推**下界估计**）
- ❌ 不做跨账号/跨机器的额度汇总（额度是本账号 × 本机维度）

## 执行流程

### 第 1 步：定位脚本

脚本位于本 Skill 目录下的 `scripts/quota_guard.py`（自包含入口，无需安装）。

先确定脚本绝对路径：

```bash
SKILL_DIR="<本 SKILL.md 所在目录>"
python "$SKILL_DIR/scripts/quota_guard.py" status
```

### 第 2 步：按用户意图选择子命令

| 用户意图 | 子命令 |
| --- | --- |
| 「我还剩多少额度」「哪些模型快满了」 | `status` |
| 「把限额规则列出来」 | `analyze` |
| 「生成交接文档」（429 中断时） | `handoff` |
| 「打开看板看看」 | `dashboard` |
| 「全套产物都出一份」 | `all` |

### 第 3 步：解读并汇报

运行 `status` 后，按以下规则向用户汇报：

1. **优先报 `risk` 为 `high` 的模型** —— 这是最可能马上撞 429 的；
2. 给出 `usage_ratio`（已用占比）与 `next_reset`（下次重置时刻）；
3. 若某模型 `limit_*` 为 `None` / `usage_ratio` 未知，说明**该模型限额样本不足**，如实告知「暂无法估算」，不要编造数字；
4. 若用户提到「任务被 429 打断了」，立即追加执行 `handoff` 生成交接文档。

## 常用命令

```bash
# 当前配额状态（人类可读）
python scripts/quota_guard.py status

# 当前配额状态（JSON，便于后续处理）
python scripts/quota_guard.py status --json

# 生成 429 交接文档
python scripts/quota_guard.py handoff --out ./handoffs

# 生成 HTML 看板
python scripts/quota_guard.py dashboard --out ./out

# 全套产物
python scripts/quota_guard.py all --out ./out
```

## 输出产物

| 文件 | 用途 |
| --- | --- |
| `limits.json` | 限额规则表（全局阈值 + 每模型限额 + 错误码统计） |
| `status.json` | 当前滚动窗口各模型用量占比与风险等级 |
| `quota_report.html` | 可视化看板，浏览器直接打开 |
| `handoff.md` | 五段式断点交接文档 |

## 汇报模板

```
📊 当前配额状态（滚动窗口 24.0h）

🔴 高风险
   hy4-preview         已用 174.8% | 下次重置 2026-10-25 23:00:00 | 已触发 429 53 次
   deepseek-v4.1-flash 已用 113.5% | 下次重置 2026-10-25 22:26:39 | 已触发 429 35 次

🟢 安全
   hy3                 已用 6.1%  | 下次重置 未知

⚠️ 建议：hy4-preview 与 deepseek-v4.1-flash 当前已超估计限额，
   新任务建议换用额度更充裕的模型，或等待下次重置。
```

> 注意：`usage_ratio > 100%` 说明**当前窗口消耗已超过历史估算限额**，属于常态现象（限额是下界估计），但确实是撞 429 的高风险信号。

## 方法论参考

更详细的反推原理、窗口语义、模型归因规则见 `references/methodology.md`；
错误码语义表见 `references/error-codes.md`。

## 隐私约束

- 只读访问本地文件，**绝不写入或删除** WorkBuddy 数据目录；
- 生成产物默认写到 `--out` 指定目录，**不要**写进用户的知识库仓库；
- 产物含本机统计信息，**不要**主动贴到公开场合或提交到 Git。

## 详细文档

面向使用者的完整说明见本 Skill 目录下的 `使用说明.md`。
