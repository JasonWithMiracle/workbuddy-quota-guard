#!/usr/bin/env python3
"""仓库卫生检查（隐私 + 产物管控）。

本脚本同时用于：
  1. 本地开发时自检：``python scripts/hygiene_check.py``
  2. CI 的 ``Privacy hygiene`` job：``python scripts/hygiene_check.py``

检查项
------
H1  仓库根目录不得提交本机生成的产物（limits.json / status.json / handoff.md / 看板）
H2  受版本控制的文件中不得出现本机绝对路径
H3  受版本控制的文件中不得出现用户账号 UUID
H4  受版本控制的文件中不得出现访问令牌 / 密钥字面量

设计说明
--------
早期版本用 shell 正则 ``git grep -nIE 'C:\\\\Users|/Users/[a-z]|JasonWithMiracle|...'`` 实现，
结果把公开仓库的**作者署名与仓库 URL**（JasonWithMiracle）误判为隐私泄漏，CI 假失败。
本脚本改为白名单式精确匹配：只拦真正的「本机痕迹」，不拦公开标识。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 根目录不允许出现的本地产物
FORBIDDEN_ARTIFACTS = [
    "limits.json",
    "status.json",
    "handoff.md",
    "quota_report.html",
    "dashboard.html",
]

#: 本机绝对路径特征（Windows / macOS / Linux home）
PATH_PATTERNS = [
    (re.compile(r"[A-Za-z]:[\\/]Users[\\/]"), "Windows 用户目录绝对路径"),
    (re.compile(r"/Users/[A-Za-z0-9._-]+/"), "macOS 用户目录绝对路径"),
    (re.compile(r"/home/[A-Za-z0-9._-]+/"), "Linux home 绝对路径"),
    (re.compile(r"ObsidianCatalogue"), "本机 vault 路径"),
]

#: 账号 UUID
UUID_PATTERN = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
                           re.IGNORECASE)

#: 令牌 / 密钥字面量（只看明显的赋值形态，避免误伤占位符）
SECRET_PATTERNS = [
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "GitHub token"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "GitHub fine-grained PAT"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "OpenAI-style key"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key id"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "私钥文件内容"),
]

#: 允许出现上述特征的文件（示例数据、说明文档中引用格式说明）
ALLOWLIST = {
    "scripts/hygiene_check.py",
}


def run(cmd):
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def tracked_files():
    code, out = run(["git", "ls-files"])
    if code != 0:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def check_artifacts():
    """H1：根目录本地产物是否被提交。"""
    problems = []
    files = set(tracked_files())
    for name in FORBIDDEN_ARTIFACTS:
        if name in files:
            problems.append("本地产物被提交进仓库：%s" % name)
    return problems


def scan_content():
    """H2/H3/H4：扫描受跟踪文件内容。"""
    problems = []
    for rel in tracked_files():
        norm = rel.replace("\\", "/")
        if norm in ALLOWLIST:
            continue
        full = os.path.join(ROOT, rel)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, encoding="utf-8", errors="ignore") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            for pat, desc in PATH_PATTERNS:
                if pat.search(line):
                    problems.append("%s:%d  %s → %s" % (norm, i, desc, line.strip()[:120]))
            if UUID_PATTERN.search(line):
                problems.append("%s:%d  账号 UUID → %s" % (norm, i, line.strip()[:120]))
            for pat, desc in SECRET_PATTERNS:
                if pat.search(line):
                    problems.append("%s:%d  疑似 %s → %s" % (norm, i, desc, line.strip()[:120]))
    return problems


def main():
    problems = check_artifacts() + scan_content()
    if problems:
        print("::error::仓库卫生检查未通过：")
        for p in problems:
            print("  - %s" % p)
        return 1
    print("仓库卫生检查通过：无本地产物、无本机绝对路径、无账号 UUID、无密钥字面量。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
