# 校准数据池（Calibration Pool）

> 限额是**统计下界**估计，精度需要社区共同校准。
> 本目录承载校准样本与聚合出的参考限额表，随开源版本分发给所有使用者。

## 为什么需要它

本工具通过「该模型触发 429 时的滚动窗口内自身消耗」反推限额。这带来两个固有误差：

1. **下界偏差**：429 只在**已经超限**后才触发，所以量到的是「至少消耗了这么多」，真实限额可能更高；
2. **样本稀疏**：模型使用得少时，触发次数少，中位数不稳定。

社区校准池的思路是：**每个使用者把自己实测到的样本贡献出来，只有经过多人交叉验证的值才进入参考表**，
从而把单机估计变成群体共识。

---

## 采纳规则

一条样本要进入 `reference.json`（随版本发布给所有人使用），必须同时满足：

| 条件 | 说明 |
|---|---|
| **≥2 个独立贡献者** | 同一贡献者的多条样本会被折叠成一条代表值，不构成交叉验证 |
| **相对离散度 ≤ 25%** | 同一维度上各贡献者代表值相对中位数的偏差上限 |
| **剔除少数派离群者** | 相对偏差超过 3 倍容差的贡献者会被剔除；但若离群者是多数派，则不剔除，判为整体冲突 |

人工锚点（`method.kind = manual-anchor`，即已知确切消耗时触发 429）在取值时**权重为自动反推的 3 倍**，
但同样需要交叉验证 —— 一个人的人工锚点不能独立定论。

未满足条件的样本不会丢失，会落到聚合结果的 `conflicts` 区，标注分歧原因，供人工研判。

---

## 怎么贡献一条样本

### 方式一：从本机自动生成（最简单）

```bash
python -m wb_quota_guard calibrate sample --contributor <你的匿名标识>
```

会在 `quota-guard-out/calibration-sample/` 下生成一条 JSON。默认取本机样本最多的模型；
指定模型用 `--model <模型名>`。

生成后建议**人工修正** `limits` 里的数值为你确知的值，并把 `method.kind` 改为 `manual-anchor`：

```json
{
  "schema_version": 1,
  "contributor": { "id": "your-id", "kind": "human" },
  "model": "hy4-preview",
  "observed_at": "2026-10-25",
  "window_hours": 24.0,
  "limits": { "tokens_M": 50.0, "credit": 112.0, "requests": 700 },
  "method": {
    "kind": "manual-anchor",
    "samples": 12,
    "notes": "已知用满 50M token 时触发 429"
  }
}
```

### 方式二：手写

照着 [`schema/sample.schema.json`](schema/sample.schema.json) 写即可。字段说明：

| 字段 | 必填 | 说明 |
|---|---|---|
| `schema_version` | ✅ | 固定为 `1` |
| `contributor.id` | ✅ | 匿名标识，3–40 位小写字母/数字/连字符。**不要用邮箱** |
| `contributor.kind` | ✅ | `human`（人工实测）/ `inferred`（工具反推） |
| `contributor.client_version` | | 客户端版本，限额可能随版本调整 |
| `model` | ✅ | 模型标识，与日志中的 `providerData.model` 一致 |
| `observed_at` | ✅ | `YYYY-MM-DD`，不精确到时分以减少可识别性 |
| `window_hours` | ✅ | 限额对应的滚动窗口长度（实测多为 24） |
| `limits` | ✅ | 至少一项非零。可用维度：`tokens_M` / `credit` / `requests` |
| `method.kind` | ✅ | `manual-anchor` / `auto-inferred` |
| `method.samples` | ✅ | 结论背后的 429 事件数，越多越可信 |
| `method.notes` | | 补充说明（≤500 字符） |
| `source.issue` | | 关联的 GitHub issue 编号，便于讨论追溯 |

---

## 隐私要求（强制）

样本会被**公开**在仓库里，因此：

- ❌ 不得包含账号 UUID、邮箱、会话内容、请求内容
- ❌ 不得包含本机绝对路径（`C:\Users\...`、`/Users/...`、`/home/...`）
- ❌ 不得包含任何密钥字面量
- ✅ `contributor.id` 用 GitHub 用户名或自选别名即可，**不必提供真实身份**

CI 会自动拦截上述内容（`scripts/calibration_check.py` 与 `scripts/hygiene_check.py` 双层校验）。

---

## 提交流程

1. 把样本文件放到 `calibration/samples/`，文件名建议 `<贡献者id>-<模型名>.json`
2. 本地自检：

   ```bash
   python scripts/calibration_check.py
   ```

3. 提 PR，CI 会跑：样本 schema 校验 → 隐私扫描 → 聚合一致性 → 参考表是否需要重新生成
4. 合并后，**月度发布工作流**（每月 1 日）会自动聚合并发布数据版本

若 CI 提示 `reference.json` 与样本集不一致，执行：

```bash
python -m wb_quota_guard calibrate publish --calibration-dir calibration --out .
```

然后提交 `calibration/reference.json`。

---

## 参考表如何被使用

`calibration/reference.json` 随版本分发。运行 `status` 时会自动加载：

- 参考表中已采纳的模型 → **以社区参考值为准**，输出里 `limit_source` 标为 `community-reference`，并附 `confidence`
- 仅本地有样本的模型 → 用本机反推值，标为 `local-inferred`
- 参考表中存在冲突的模型 → 仍用本机反推值，标为 `local-inferred (conflicted)`

---

## 相关命令速查

```bash
python -m wb_quota_guard calibrate sample --contributor <id>   # 生成本机样本
python -m wb_quota_guard calibrate validate                     # 校验样本
python -m wb_quota_guard calibrate aggregate                     # 聚合并查看采纳/冲突
python -m wb_quota_guard calibrate publish --out .              # 生成参考表
python scripts/calibration_check.py                             # CI 门禁本地复现
```
