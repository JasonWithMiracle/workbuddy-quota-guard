"""WorkBuddy 配额感知器（wb-quota-guard）。

从本地 WorkBuddy 会话日志反推各模型的独立限额，在任务被 429 强制终止之前
给出「还剩多少」的判断依据，被终止时生成可直接交接给下一个模型的文档。

只读分析，不修改 WorkBuddy 的任何数据。
"""

__version__ = "1.0.1"
__all__ = ["core", "report", "cli"]


def _lazy(name):
    """延迟导入，避免 ``python -c "import wb_quota_guard"`` 就扫盘。"""
    import importlib
    return importlib.import_module(f"{__name__}.{name}")


def __getattr__(name):
    if name in ("core", "report", "cli"):
        return _lazy(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
