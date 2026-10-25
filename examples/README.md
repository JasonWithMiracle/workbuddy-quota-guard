# workbuddy-quota-guard

WorkBuddy / CodeBuddy **配额感知器**：反推各模型 429 限额、预警剩余额度、生成断点交接文档。

- 完整文档：[README.md](README.md)
- 方法论：[docs/architecture.md](docs/architecture.md) ｜ [docs/error-codes.md](docs/error-codes.md) ｜ [docs/roadmap.md](docs/roadmap.md)
- Skill 使用说明：[skill/使用说明.md](skill/使用说明.md)
- 姊妹项目：[workbuddy-token-audit](https://github.com/JasonWithMiracle/workbuddy-token-audit)（成本维度）

## 快速开始

```bash
python -m wb_quota_guard status        # 当前配额状态
python -m wb_quota_guard all --out ./out
```

零第三方依赖，Python 3.9+。

## 隐私

只读访问本地日志；所有生成产物均在 `.gitignore` 中，**不随仓库分发**。
