# WorkBuddy / CodeBuddy 业务错误码语义表

> 逆向自客户端错误处理逻辑（`lookupBizCode` / `classifyErrorDetail`）。

## 目录

- [quota 类：真限额](#quota-类真限额)
- [queue 类：排队而非限额](#queue-类排队而非限额)
- [速查表](#速查表)

---

## quota 类：真限额

| 错误码 | 子类 | 中文含义 | 处理建议 |
| --- | --- | --- | --- |
| `6004` | `quota_token_limit` | Token 频率限额（按窗口重置） | 换模型或等重置 |
| `6000` | `quota_token_limit` | Token 频率限额（按窗口重置） | 换模型或等重置 |
| `6005` | `quota_request_limit` | 请求数频率限额 | 降低请求频率 |
| `6006` | `quota_request_limit` | 请求数频率限额 | 降低请求频率 |
| `6007` | `quota_request_limit` | 请求数频率限额 | 降低请求频率 |
| `6008` | `quota_request_limit` | 请求数频率限额 | 降低请求频率 |
| `14003` | `quota_request_limit` | 请求数限额 | 降低请求频率 |

---

## quota 类：非速率限制（易误判）

| 错误码 | 子类 | 中文含义 | 处理建议 |
| --- | --- | --- | --- |
| `14001` | `quota_balance_exhausted` | 余额耗尽 | **需充值**，等重置无用 |
| `14002` | `quota_balance_exhausted` | 余额耗尽 | **需充值**，等重置无用 |
| `14012` | `quota_balance_exhausted` | 余额耗尽 | **需充值**，等重置无用 |
| `14013` | `quota_balance_exhausted` | 余额耗尽 | **需充值**，等重置无用 |
| `14014` | `quota_balance_exhausted` | 余额耗尽 | **需充值**，等重置无用 |
| `14018` | `quota_balance_exhausted` | 余额耗尽 | **需充值**，等重置无用 |
| `10105` | `quota_active_session` | 活跃会话超限 | 关闭其他会话 |
| `15001` | `quota_web_search` | 联网搜索额度耗尽 | 关闭联网搜索 |
| `14016` | `quota_not_activated` | 套餐未激活 | 激活套餐 |
| `14017` | `quota_not_activated` | 套餐未激活 | 激活套餐 |

> ⚠️ **最常见的误判**：把 `quota_balance_exhausted` 当成速率限制。
> 前者需要充值，后者等窗口重置即可。分类错误会导致完全错误的行动建议。

---

## queue 类：排队而非限额

| 错误码 | 子类 | 中文含义 | 处理建议 |
| --- | --- | --- | --- |
| `6020` | `queue_waiting` | 排队中（等待位） | 等待即可，**不是限额** |
| `6021` | `queue_full` | 队列已满 | 稍后重试 |
| `6022` | `queue_user_limit` | 用户并发超限 | 降低并发 |

> 排队类错误**不消耗配额**，不应计入「限额反推」的样本。
> 本项目的 `build_model_limits()` 只取 `category == "quota"` 且带 `reset` 时刻的事件作为样本。

---

## 速查表

按语义分组：

```
quota_token_limit      → 6004, 6000
quota_request_limit    → 6005, 6006, 6007, 6008, 14003
quota_balance_exhausted→ 14001, 14002, 14012, 14013, 14014, 14018
quota_active_session   → 10105
quota_web_search       → 15001
quota_not_activated    → 14016, 14017
queue_waiting          → 6020
queue_full             → 6021
queue_user_limit       → 6022
```

其中**只有** `quota_token_limit` 与 `quota_request_limit` 是「速率/频率限额」，
也是本项目限额反推真正关心的两类。

---

## 与客户端行为的对应

本表与客户端渲染层的错误提示逻辑一致，因此：

- `limits.json` 的 `code_stats` 字段按错误码统计本机历史命中次数；
- `error_code_catalog` 字段内嵌完整语义表，便于下游程序按 `category` 分流处理。
