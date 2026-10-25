#!/usr/bin/env python3
"""workbuddy-quota-guard Skill 入口。

用法::

    python scripts/quota_guard.py status
    python scripts/quota_guard.py status --json
    python scripts/quota_guard.py handoff --out ./handoffs
    python scripts/quota_guard.py all --out ./out

该文件是 Skill 包的自包含入口：把 ``wb_quota_guard`` 包目录加入 ``sys.path``，
因此无需安装即可直接运行。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wb_quota_guard.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
