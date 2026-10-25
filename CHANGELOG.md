# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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
