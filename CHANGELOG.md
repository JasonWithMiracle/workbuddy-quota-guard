# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.1.0] - 2026-10-25

### 新增 —— 社区校准池

限额是统计下界，精度需要社区共同校准。本版本引入开放的校准数据池机制。

- **校准样本格式**：`calibration/schema/sample.schema.json`，JSON Schema 定义，含隐私约束
- **聚合引擎**：`src/wb_quota_guard/calibration.py`
  - 样本校验（schema + 逐值隐私扫描）
  - **先按贡献者折叠样本**，再做一致性判断 —— 防止「一人多提」压过他人
  - **少数派离群者剔除** —— 单个异常提交不会让某模型对所有人不可用
  - 加权中位数（人工锚点权重 ×3），等权时严格等于普通中位数
  - 置信度打分（贡献者数 / 离散度 / 是否含人工锚点）
- **CLI 子命令**：`calibrate sample` / `validate` / `aggregate` / `publish`
- **参考限额表**：`calibration/reference.json` 随版本分发，`status` 自动加载并优先采用
  - 输出中 `limit_source` 标注来源，`confidence` 给出可信度
- **CI 门禁**：`scripts/calibration_check.py`（样本校验 + 参考表与样本集一致性）
- **月度发布工作流**：`.github/workflows/monthly-calibration.yml`
  - 每月 1 日自动检查，有变更才发版，无变更静默跳过
  - 版本号形如 `v2026.10`，与功能版本解耦
- **单元测试**：`tests/test_calibration.py`，29 个用例覆盖校验、聚合、离群剔除、合并

### 采纳规则

一条样本进入参考表需同时满足：

1. 同一模型同一窗口下，**≥2 个独立贡献者**；
2. 剔除少数派离群者后，各维度相对离散度 **≤ 25%**；
3. 人工锚点（已知确切消耗时触发 429）权重为自动反推的 **3 倍**，但仍需交叉验证。

未达标样本落入 `conflicts` 区，标注分歧原因，不丢弃。

### 修复

- **校准样本隐私扫描改为逐值扫描**：此前扫描 `json.dumps` 后的字符串，反斜杠被转义，
  导致 Windows 绝对路径漏检
- **参考表合并的建键错误**：参考表主键为「模型@窗口h」，而消费方按纯模型名索引，
  导致同一模型被拆成两条
- **加权中位数在等权时不等于普通中位数**：改为按权重量化展开，保证等权情形与普通中位数一致

## [1.0.1] - 2026-10-25

修复 v1.0.0 发布时暴露的 CI 配置缺陷。**不含功能变更**，分析引擎与产物格式完全不变。

### 修复

- **CI：Python 3.9 任务在 Set up Python 步骤失败**
  根因：GitHub 默认 runner 已切换到 `ubuntu-26.04`，而 `actions/python-versions` 中 Python 3.9–3.13 均无 26.04 的 linux/x64 构建（3.9 仅有 20.04 / 22.04 / 24.04），`setup-python` 直接报 not found。
  修法：测试矩阵改为 `include` 形式并显式钉住 `ubuntu-24.04`。
  说明：源码本身无 `match` 语句、无 PEP 604 联合类型、无 3.10+ 专属 API，属纯环境问题。

- **CI：Privacy hygiene 任务误报**
  根因：shell 正则把 `JasonWithMiracle` 当作隐私标识拦截，但它只是公开仓库的作者署名与仓库 URL，命中 `docs/roadmap.md`、`skill/使用说明.md` 均为正常内容。
  修法：抽为 `scripts/hygiene_check.py`，白名单式精确匹配 Windows / macOS / Linux 用户目录绝对路径、vault 本机路径、账号 UUID 与密钥字面量；该脚本可本地直接运行复现，不再依赖 shell 正则。

### 变更

- Hook 脚本 `hooks/quota-precheck.js` 改为路径自适应：Skill 安装目录与仓库目录均可定位分析脚本
- README 补充 CI 状态 badge
- README「贡献」章节补充**维护者发布流程**，明确「CI 全绿后才能打 tag」，
  以及「已发布 tag 有缺陷时用补丁版本收口、不 force-push tag」的处置方式

## [1.0.0] - 2026-10-25

首个公开版本。

### 新增

- **限额反推引擎**：从本地会话日志中的 429 事件反推「账号 × 模型」独立限额（滚动窗口口径）
- **模型归因**：429 错误行不带模型字段，使用会话邻近法（429 前 120 秒内最近一次成功请求）推断
- **错误码目录**：完整逆向客户端业务错误码语义表（quota 7 类 + queue 3 类）
- **状态评估**：按模型独立的滚动 24h 窗口估算当前用量占比与风险等级
- **HTML 看板**：自适应暗色可视化看板（`quota_report.html`）
- **断点交接文档**：五段式 `handoff.md`，用于 429 中断时交接给下一个模型
- **Python 包化**：从单文件重构为 `core` / `report` / `cli` 三层，零第三方依赖
- **命令行入口**：`analyze` / `status` / `handoff` / `dashboard` / `all` / `db` 六个子命令
- **WorkBuddy Skill 包**：可安装到 `~/.workbuddy/skills/`，自然语言调用
- **Hook 预警脚本**：`SessionStart` 阶段自动检查高风险模型
- **CI**：Python 多版本（3.9–3.13）语法与导入检查

### 隐私

- 全程只读访问本地文件，不修改任何 WorkBuddy 数据
- 所有生成产物默认在 `.gitignore` 中，**不随仓库分发**
- 仓库不含个人标识、账号 ID、会话内容

### 已知限制

- 限额为**统计下界**估计，样本少的模型可信度低
- 无法获取真实剩余额度（客户端令牌存于 Electron DPAPI 安全存储）
- 仅适配 WorkBuddy / CodeBuddy 桌面客户端
