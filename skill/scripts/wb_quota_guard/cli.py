"""命令行入口。

用法::

    python -m wb_quota_guard status        # 当前各模型配额状态
    python -m wb_quota_guard analyze       # 反推限额规则表
    python -m wb_quota_guard handoff       # 生成 429 交接文档
    python -m wb_quota_guard dashboard     # 生成 HTML 看板
    python -m wb_quota_guard all           # 全部执行
    python -m wb_quota_guard db            # 导出会话级 credit 明细
"""
import argparse
import json
import os
import sys

from . import core
from . import report

DEFAULT_OUT = os.path.join(os.getcwd(), "quota-guard-out")


def _dump(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    return path


def _build_parser():
    p = argparse.ArgumentParser(
        prog="wb-quota-guard",
        description="WorkBuddy 配额感知器：反推各模型限额、预警剩余额度、生成 429 交接文档。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python -m wb_quota_guard status\n"
            "  python -m wb_quota_guard handoff --out ./handoffs\n"
        ),
    )
    p.add_argument(
        "command",
        choices=["analyze", "status", "handoff", "dashboard", "all", "db"],
        help="要执行的动作",
    )
    p.add_argument(
        "-o", "--out",
        default=DEFAULT_OUT,
        help=f"产物输出目录（默认 {DEFAULT_OUT}）",
    )
    p.add_argument(
        "--home",
        default=None,
        help="WorkBuddy 数据根目录（默认 ~/.workbuddy，可用 WORKBUDDY_HOME 覆盖）",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="status 命令下以 JSON 输出到 stdout（便于被 hook / 脚本消费）",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="仅输出错误信息",
    )
    return p


def _apply_home(path):
    if path:
        core.WB = os.path.abspath(path)
        core.PROJECTS = os.path.join(core.WB, "projects")
        core.DB = os.path.join(core.WB, "workbuddy.db")


def main(argv=None):
    args = _build_parser().parse_args(argv)
    _apply_home(args.home)
    os.makedirs(args.out, exist_ok=True)

    def say(*a):
        if not args.quiet:
            print(*a)

    # dashboard 的 CSS 常量原本定义在旧单文件里，这里从 report 模块取
    if args.command == "db":
        for row in core.read_db_credits()[:20]:
            print(row)
        return 0

    limits, usage, events = core.analyze()
    _dump(os.path.join(args.out, "limits.json"), limits)
    say(f"[analyze] 扫描 {limits['totals']['files_scanned']} 个会话文件 | "
        f"请求 {limits['totals']['requests_with_usage']:,} | "
        f"429 事件 {limits['totals']['events_429']} | "
        f"窗口 {limits['thresholds']['window_hours_median']}h")
    say(f"[analyze] 全局中位限额：Token {limits['thresholds']['token_limit_M']}M | "
        f"Credit {limits['thresholds']['credit_limit']} | "
        f"请求 {limits['thresholds']['request_limit']}")

    if args.command == "analyze":
        say(f"[analyze] 已写出 {os.path.join(args.out, 'limits.json')}")
        return 0

    status = core.current_status(limits, usage, events)
    _dump(os.path.join(args.out, "status.json"), status)

    if args.command == "status":
        if args.json:
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 0
        say(f"[status] 滚动窗口 {status['window_hours']}h，"
            f"{len(status['models'])} 个模型有消耗：")
        for w in status["models"][:15]:
            ratio = w["usage_ratio"]
            ratio_s = f"{ratio:.0%}" if ratio is not None else "未知"
            say(f"   {w['model']:26s} 已用 {w['total_tokens']/1e6:7.2f}M token | "
                f"占比 {ratio_s:>5s} | 风险 {w['risk']:8s} | 重置 {w['next_reset']}")
        return 0

    if args.command == "dashboard":
        path = report.gen_dashboard(limits, status, out_dir=args.out)
        say(f"[dashboard] 已写出 {path}")
        return 0

    if args.command == "handoff":
        path = report.gen_handoff(limits, status, events, out_dir=args.out)
        say(f"[handoff] 已写出 {path}")
        return 0

    # all
    say(f"[analyze] 已写出 {os.path.join(args.out, 'limits.json')} / status.json")
    say(f"[dashboard] 已写出 {report.gen_dashboard(limits, status, out_dir=args.out)}")
    say(f"[handoff] 已写出 {report.gen_handoff(limits, status, events, out_dir=args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
