# workbuddy-quota-guard

> WorkBuddy / CodeBuddy **配额感知器**：在 429 把你的任务打断之前，先告诉你「这个模型还剩多少额度」。

![license](https://img.shields.io/badge/license-MIT-green)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
![deps](https://img.shields.io/badge/dependencies-0-brightgreen)

---

## 这是什么问题

在 WorkBuddy 里跑复杂任务时，你可能遇到过这样的场景：

- 任务跑到一半，模型直接返回 **429**，任务被硬生生打断；
- 客户端界面**看不到**「这个模型我还剩多少额度」，只能等撞墙才知道；
- 被迫中断 → 换模型 → 重跑 → 质量不可控 + 大量重复劳动。

问题不在于「你没充钱」，而在于**限额是账号 × 模型粒度的独立配额**，且以滚动 24 小时窗口重置。客户端不暴露剩余量，CLI 层也不返回任何 `rate-limit` 响应头，于是用户完全失去了「提前规划」的依据。

**本项目做的事**：只读解析本地会话日志，反推每个模型的历史限额，用同一套窗口语义估算当前剩余额度，输出预警 + 看板 + 断点交接文档。

---

## 核心特性

| 能力 | 说明 |
|---|---|
| 🔍 限额反推 | 从历史 429 事件反推「账号 × 模型」独立限额（滚动窗口口径） |
| 📊 实时状态 | 按当前滚动窗口估算各模型已用占比 + 风险等级（low / medium / high） |
| 📄 断点交接 | 一键生成 `handoff.md`，429 中断时直接交给下一个模型接手 |
| 🖥️ 可视化看板 | 自适应暗色 HTML 看板，浏览器直接打开 |
| 🪝 Hook 预警 | 会话启动时自动检查，高风险模型提前喊话 |
| 🧩 Skill 化 | 可作为 WorkBuddy Skill 安装，一条指令调用 |
| 🔒 隐私优先 | **只读**本地文件；代码公开、**数据永不入库** |

---

## 安装

```bash
git clone https://github.com/JasonWithMiracle/workbuddy-quota-guard.git
cd workbuddy-quota-guard

# 方式一：直接运行（零依赖，纯标准库）
python -m wb_quota_guard status

# 方式二：安装为命令行工具
pip install -e .
wb-quota-guard status
```

要求 Python 3.9+，**无任何第三方依赖**。

---

## 快速开始

```bash
# 查看当前各模型配额状态
python -m wb_quota_guard status

# 输出 JSON（供 hook / 脚本消费）
python -m wb_quota_guard status --json

# 反推限额规则表 -> limits.json
python -m wb_quota_guard analyze

# 生成 HTML 看板 + Markdown 交接文档
python -m wb_quota_guard dashboard
python -m wb_quota_guard handoff

# 全套产物
python -m wb_quota_guard all --out ./out
```

### 产物

| 文件 | 用途 |
|---|---|
| `limits.json` | 限额规则表：全局阈值 + 每模型限额 + 错误码统计 |
| `status.json` | 当前滚动窗口各模型用量占比与风险等级 |
| `quota_report.html` | 可视化看板（浏览器直接打开，暗色自适应） |
| `handoff.md` | 五段式断点交接文档 |

---

## 工作原理

### 1. 限额是怎么反推出来的

1. 扫描 `~/.workbuddy/projects/**/*.jsonl`，抽取每条请求的 usage（model / token / credit）与时间戳；
2. 抽取所有 **429 事件**及其携带的「窗口重置时刻」`reset`；
3. 对某个模型的**每一个** 429 事件，取 `[reset - W, reset]` 区间内**仅该模型自身**的消耗——`W` 取全库观察到的窗口长度中位数（实测 ≈ 24.01h）；
4. 多个样本取**中位数** → 该模型的限额**下界估计**。

> 为什么是「下界」：被限流说明「至少」已经消耗到超限，所以样本值只会低估真实限额。

### 2. 窗口语义

WorkBuddy 的限额窗口是**滚动 24 小时**，不是固定时段的自然日。实测相邻两次 reset 的间隔中位数为 **24.01 小时**，与滚动窗口高度吻合。

因此当前状态按 `now - W < ts <= now` 统计**每个模型自己**的消耗，与该模型自己的历史限额对比。

### 3. 模型归因

429 错误行本身不带 `model` 字段。本项目用**会话邻近法**推断：取同会话内、该 429 之前 120 秒内**最近一次成功请求**所用的模型；找不到时回退到会话内的多数模型。

### 4. 错误码分类

客户端的业务错误码被逆向出一张完整的语义表（`quota` 7 类 + `queue` 3 类），完整清单见 [`docs/error-codes.md`](docs/error-codes.md)。

---

## 数据来源与隐私

本项目**只读**访问以下本地路径：

| 路径 | 内容 |
|---|---|
| `~/.workbuddy/projects/**/*.jsonl` | 会话日志（含逐请求 usage） |
| `~/.workbuddy/workbuddy.db` | 会话级 credit 明细（**需连同 `-wal` / `-shm` 一起复制**再连，否则漏数据） |

**保证**：

- ✅ 只读，不修改、不删除任何 WorkBuddy 数据；
- ✅ 不上传任何数据到网络；
- ✅ **生成的产物（`limits.json` / `status.json` / `handoff.md` / 看板）默认在 `.gitignore` 中，不入库**；
- ✅ 仓库内不含任何个人标识、账号 ID、会话内容。

可用 `--home <path>` 或环境变量 `WORKBUDDY_HOME` 指定其他数据根目录。

---

## 集成方式

### A. 作为 WorkBuddy Skill

把 [`skill/`](skill/) 目录安装到 `~/.workbuddy/skills/workbuddy-quota-guard/`，即可用自然语言调用：

> 「查一下我现在各模型还剩多少额度」
> 「生成一份 429 交接文档」

详见 [`skill/使用说明.md`](skill/使用说明.md)。

### B. Hook 自动预警

把 [`hooks/quota-precheck.js`](hooks/quota-precheck.js) 注册到 `~/.workbuddy/settings.json` 的 `SessionStart` 事件，会话一开始就检查额度并提示高风险模型：

```jsonc
{
  "hooks": {
    "SessionStart": [
      {
        "type": "command",
        "command": "node /absolute/path/to/hooks/quota-precheck.js",
        "timeout": 20
      }
    ]
  }
}
```

---

## 已知限制

| 限制 | 说明 |
|---|---|
| ⚠️ 限额是**统计下界** | 样本少的模型（如仅 3 次触发）可信度低，需实际触发后校准 |
| ⚠️ **拿不到真实剩余额度** | 客户端登录态存在 Electron DPAPI 安全存储中，外部脚本读不到；本项目只能从日志反推 |
| ⚠️ 依赖本地日志 | 清空会话日志会导致历史限额样本丢失，需重新积累 |
| ⚠️ 平台相关 | 目前针对 WorkBuddy / CodeBuddy 桌面客户端，其他客户端不适用 |

> **关于实时额度接口**：逆向发现客户端渲染层存在 `POST /billing/meter/get-user-resource-summary`（返回 `usageTotal` / `usageLeft` / `refreshAt`）等接口，但认证令牌存放于 Electron DPAPI 加密存储，外部脚本无法获取。这条路径的可行解法记录在 [`docs/roadmap.md`](docs/roadmap.md) 中（渲染层内执行 / IPC 透传）。

---

## 项目结构

```
workbuddy-quota-guard/
├── src/wb_quota_guard/       # Python 包（纯标准库）
│   ├── core.py               # 扫描 / 限额反推 / 状态评估
│   ├── report.py             # 看板 + 交接文档生成
│   ├── cli.py                # 命令行入口
│   └── __main__.py
├── skill/                    # WorkBuddy Skill 包
│   ├── SKILL.md
│   ├── 使用说明.md
│   └── references/
├── hooks/                    # Hook 脚本（Node）
├── docs/                     # 方法论 / 错误码 / Roadmap
├── examples/                 # 示例产物（脱敏）
└── .github/workflows/        # CI
```

---

## 相关项目

- [`workbuddy-token-audit`](https://github.com/JasonWithMiracle/workbuddy-token-audit) —— WorkBuddy 本地 token 用量审计与成本归因（本项目是其**配额维度**的姊妹项目）

---

## 贡献

欢迎提 Issue / PR。特别欢迎：

- 不同 WorkBuddy 版本的**限额实测校准数据**（能显著提升反推精度）；
- 其他平台（如 CodeBuddy CLI）的适配；
- 真实剩余额度接口的可行方案。

---

## License

[MIT](LICENSE) © 2026 JasonWithMiracle
