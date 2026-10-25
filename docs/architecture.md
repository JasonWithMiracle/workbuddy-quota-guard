# 架构与实现说明

## 分层

```
┌─────────────────────────────────────────────┐
│  cli.py          命令行入口 / 参数解析         │
│                  子命令分发、产物落盘           │
├─────────────────────────────────────────────┤
│  report.py       产物生成层                    │
│                  HTML 看板、Markdown 交接文档   │
├─────────────────────────────────────────────┤
│  core.py         分析层（无副作用）             │
│                  扫描 → 归因 → 反推 → 评估      │
└─────────────────────────────────────────────┘
                    │
                    ▼
        ~/.workbuddy/projects/**/*.jsonl   （只读）
        ~/.workbuddy/workbuddy.db          （只读，复制副本后连接）
```

**关键设计约束**：`core.py` **不写任何文件**，只返回数据结构。所有落盘由 `cli.py` 负责。这样：

- 分析逻辑可被 Hook、Skill、CI 复用而不产生副作用；
- 测试时无需清理临时文件；
- `--out` 目录语义统一。

---

## 数据流

```
sessions/**/*.jsonl
      │
      ▼
   scan()  ──────────────────────────► usage[]   （逐请求：model/token/credit/ts/session）
      │        └─► events[]  （429 事件：code/reset/ts/session）
      │
      ▼
attribute_event_models()  ───────────► 给 429 事件补上 model 字段
      │
      ▼
build_windows() + annotate()  ───────► 给 usage 打上所属窗口标记
      │
      ▼
build_model_limits()  ───────────────► 每模型限额下界估计
      │
      ├───────────────────────────────► limits.json
      ▼
current_status()  ───────────────────► 滚动窗口用量占比 + 风险等级 ──► status.json
      │
      ├───────────────────────────────► quota_report.html
      └───────────────────────────────► handoff.md
```

---

## 核心算法

### 1. 模型归因 `attribute_event_models()`

```python
# 同会话内，找 429 之前 120 秒内最近的一次成功请求
lst = by_session.get(e.get("session"))
# 取 ts < e.ts 且 e.ts - ts <= 120 的最大 ts 对应模型
# 找不到 → 回退到会话内多数模型
```

### 2. 限额反推 `build_model_limits()`

```python
W = median(所有窗口长度) * 3600        # ≈ 24h
for e in events:                        # 每个 429 事件一个样本
    R = parse(e["reset"])
    seg = [u for u in usage if u["model"] == m and (R - W) < u["ts"] <= R]
    samples[m].append({credit, tokens, requests})
limits[m] = {median_credit, median_tokens_M, median_requests, max_*}
```

**只取该模型自身的消耗**——这是本项目与「混算全局阈值」的关键区别。

### 3. 状态评估 `current_status()`

```python
now = time()
W = limits["thresholds"]["window_hours_median"] * 3600
for m in 有消耗的模型:
    seg = [u for u in usage if u["model"] == m and (now - W) < u["ts"] <= now]
    ratio = 用量 / 该模型限额
    risk = "high" if ratio >= 0.85 else "medium" if ratio >= 0.5 else "low"
```

**注意**：滚动窗口是 `now - W < ts <= now`，与反推时的 `R - W < ts <= R` 是同一套语义。
早期版本曾误用「最近一次 reset 作为窗口上界」，导致已用量被算成 0、占比错误 —— 已修正。

---

## 可移植性

`core.py` 顶部的路径全部可覆盖：

```python
HOME = os.path.expanduser("~")
WB = os.environ.get("WORKBUDDY_HOME") or os.path.join(HOME, ".workbuddy")
PROJECTS = os.path.join(WB, "projects")
DB = os.path.join(WB, "workbuddy.db")
```

`cli.py` 的 `--home` 参数会覆盖这三个模块级变量。因此本工具天然支持：

- 分析其他机器同步过来的日志目录；
- 在 CI 中跑脱敏样本；
- 多 profile 并存时分别统计。

---

## 只读保证

| 访问方式 | 实现 |
| --- | --- |
| jsonl 日志 | `open(fp, encoding="utf-8", errors="ignore")` 只读 |
| SQLite | `shutil.copy2` 三件套到临时目录 → 连副本 → `finally` 删临时目录 |

```python
# core.read_db_credits() 的关键片段
tmp = tempfile.mkdtemp()
try:
    for suffix in ("", "-wal", "-shm"):
        src = DB + suffix
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(tmp, "db" + suffix))
    conn = sqlite3.connect(os.path.join(tmp, "db"))
    ...
finally:
    shutil.rmtree(tmp, ignore_errors=True)
```

**从不对 `~/.workbuddy/` 做任何写操作。**
