"""命令行入口。

用法::

    python -m wb_quota_guard status        # 当前各模型配额状态
    python -m wb_quota_guard analyze       # 反推限额规则表
    python -m wb_quota_guard handoff       # 生成 429 交接文档
    python -m wb_quota_guard dashboard     # 生成 HTML 看板
    python -m wb_quota_guard all           # 全部执行
    python -m wb_quota_guard db            # 导出会话级 credit 明细
    python -m wb_quota_guard calibrate ... # 社区校准数据池
"""
import argparse
import json
import os
import sys

from . import core
from . import report
from . import calibration

DEFAULT_OUT = os.path.join(os.getcwd(), "quota-guard-out")


def _calibration_candidates():
    """校准数据目录的候选路径。

    兼容三种形态：
      - 仓库内运行：<repo>/calibration
      - Skill 包内运行：<skill>/calibration
      - 当前工作目录下：./calibration
    """
    here = os.path.abspath(__file__)
    pkg_dir = os.path.dirname(here)               # .../wb_quota_guard
    scripts_dir = os.path.dirname(pkg_dir)        # .../scripts 或 .../src
    roots = [
        os.path.dirname(scripts_dir),             # <repo> 或 <skill>
        os.path.dirname(os.path.dirname(scripts_dir)),  # 上级（源码布局时兜底）
    ]
    cands = [os.path.join(r, "calibration") for r in roots]
    cands.append(os.path.join(os.getcwd(), "calibration"))
    return cands


def _resolve_calibration_dir(explicit=None):
    """找到实际存在的校准数据目录；都不存在则返回 None。"""
    if explicit:
        return os.path.abspath(explicit)
    for c in _calibration_candidates():
        if os.path.isdir(c):
            return os.path.abspath(c)
    return None


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
        choices=["analyze", "status", "handoff", "dashboard", "all", "db", "calibrate"],
        help="要执行的动作",
    )
    p.add_argument(
        "action",
        nargs="?",
        choices=["sample", "validate", "aggregate", "publish"],
        default=None,
        help="calibrate 命令的子动作：sample 生成本机样本 / validate 校验样本 / "
             "aggregate 聚合成参考表 / publish 导出可发布的参考表",
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
    p.add_argument(
        "--calibration-dir",
        default=None,
        help="校准数据目录（默认自动探测仓库内 calibration/）",
    )
    p.add_argument(
        "--contributor",
        default=None,
        help="校准样本的贡献者匿名标识（3-40 位小写字母/数字/连字符）",
    )
    p.add_argument(
        "--model",
        default=None,
        help="校准样本针对的模型（默认取本机样本最多的模型）",
    )
    return p


def _apply_home(path):
    if path:
        core.WB = os.path.abspath(path)
        core.PROJECTS = os.path.join(core.WB, "projects")
        core.DB = os.path.join(core.WB, "workbuddy.db")


# --------------------------------------------------------------------------
# 校准子命令
# --------------------------------------------------------------------------

def _cmd_calibrate_sample(args, say):
    """从本机反推结果生成一条校准样本（脱敏，可提交 PR）。"""
    if not args.contributor:
        print("[calibrate] 错误：生成样本必须提供 --contributor <匿名标识>", file=sys.stderr)
        return 2

    limits, _usage, _events = core.analyze()
    per_model = limits.get("per_model_limits", {})
    if not per_model:
        print("[calibrate] 本机没有可用于生成样本的 429 事件，无法生成校准样本", file=sys.stderr)
        return 1

    # 取样本数最多的模型作为代表；用户可用 --model 覆盖
    model = getattr(args, "model", None)
    if not model:
        model = max(per_model, key=lambda m: per_model[m].get("samples") or 0)
    if model not in per_model:
        print(f"[calibrate] 模型 {model} 无本地反推数据", file=sys.stderr)
        return 1

    entry = per_model[model]
    sample = {
        "schema_version": calibration.SCHEMA_VERSION,
        "contributor": {"id": args.contributor, "kind": "inferred"},
        "model": model,
        "observed_at": limits.get("generated_at", "")[:10],
        "window_hours": limits.get("thresholds", {}).get("window_hours_median") or 24.0,
        "limits": {
            "tokens_M": entry.get("median_tokens_M"),
            "credit": entry.get("median_credit"),
            "requests": entry.get("median_requests"),
        },
        "method": {
            "kind": "auto-inferred",
            "samples": entry.get("samples") or 1,
            "notes": "由 wb-quota-guard 自动反推的窗口下界样本",
        },
    }
    # 去掉值为 0 或 None 的维度
    sample["limits"] = {k: v for k, v in sample["limits"].items() if v}

    try:
        sample = calibration.validate_sample(sample, "<generated>")
    except calibration.SampleError as exc:
        print(f"[calibrate] 生成的样本未通过校验：{exc}", file=sys.stderr)
        return 1

    out_dir = os.path.join(args.out, "calibration-sample")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{args.contributor}-{model}.json")
    _dump(path, sample)
    say(f"[calibrate] 样本已写出：{path}")
    say(f"[calibrate] 模型 {model} | 窗口 {sample['window_hours']}h | "
        f"样本数 {sample['method']['samples']}")
    say("[calibrate] 提示：人工实测锚点（已知确切消耗时触发 429）请把 method.kind 改为 "
        "manual-anchor，并填准确的 limits 值")
    return 0


def _cmd_calibrate_validate(args, say):
    """校验校准样本目录下的所有样本。"""
    cal_dir = _resolve_calibration_dir(args.calibration_dir)
    if not cal_dir:
        print("[calibrate] 未找到校准数据目录（用 --calibration-dir 指定）", file=sys.stderr)
        return 1
    samples_dir = os.path.join(cal_dir, "samples")
    samples, errors = calibration.load_samples(samples_dir)
    for err in errors:
        print(f"[calibrate] ✗ {err}", file=sys.stderr)
    say(f"[calibrate] 校验完成：{len(samples)} 条通过，{len(errors)} 条失败")
    if errors:
        return 1
    models = {}
    for s in samples:
        models.setdefault(s["model"], set()).add(s["contributor"]["id"])
    for m in sorted(models):
        say(f"   {m}: {len(models[m])} 个独立贡献者")
    return 0


def _cmd_calibrate_aggregate(args, say):
    """聚合校准样本，生成参考限额表。"""
    cal_dir = _resolve_calibration_dir(args.calibration_dir)
    if not cal_dir:
        print("[calibrate] 未找到校准数据目录（用 --calibration-dir 指定）", file=sys.stderr)
        return 1
    samples, errors = calibration.load_samples(os.path.join(cal_dir, "samples"))
    for err in errors:
        print(f"[calibrate] ✗ {err}", file=sys.stderr)
    if errors:
        print("[calibrate] 存在无效样本，拒绝聚合（先修复样本）", file=sys.stderr)
        return 1

    result = calibration.aggregate(samples)
    path = os.path.join(args.out, "calibration-aggregate.json")
    _dump(path, result)
    say(f"[calibrate] 已聚合 {len(samples)} 条样本 → {path}")
    say(f"[calibrate] 采纳 {result['adopted_count']} 个模型；冲突 {len(result['conflicts'])} 个")
    for key, entry in sorted(result["reference"].items()):
        say(f"   ✅ {key}: {entry['limits']} | 贡献者 {entry['contributor_count']} | "
            f"置信度 {entry['confidence']}")
    for c in result["conflicts"]:
        say(f"   ⚠️ {c['model']}@{c['window_hours']}h：{c['reason']}")
    return 0


def _cmd_calibrate_publish(args, say):
    """导出可随开源版本分发的参考限额表。"""
    cal_dir = _resolve_calibration_dir(args.calibration_dir)
    if not cal_dir:
        print("[calibrate] 未找到校准数据目录（用 --calibration-dir 指定）", file=sys.stderr)
        return 1
    samples, errors = calibration.load_samples(os.path.join(cal_dir, "samples"))
    if errors:
        for err in errors:
            print(f"[calibrate] ✗ {err}", file=sys.stderr)
        return 1
    result = calibration.aggregate(samples)
    path = os.path.join(args.out, "reference.json")
    _dump(path, result["reference"])
    say(f"[calibrate] 参考限额表已写出：{path}（{result['adopted_count']} 个模型）")
    return 0


def _load_community_reference(args):
    """加载社区参考限额表（若存在），用于提升 status 的估算精度。"""
    cal_dir = _resolve_calibration_dir(args.calibration_dir)
    if not cal_dir:
        return None
    ref_path = os.path.join(cal_dir, "reference.json")
    if not os.path.isfile(ref_path):
        return None
    try:
        with open(ref_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


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

    if args.command == "calibrate":
        action = args.action or "validate"
        handlers = {
            "sample": _cmd_calibrate_sample,
            "validate": _cmd_calibrate_validate,
            "aggregate": _cmd_calibrate_aggregate,
            "publish": _cmd_calibrate_publish,
        }
        return handlers[action](args, say)

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

    reference = _load_community_reference(args)
    if reference:
        say(f"[calibrate] 已加载社区参考限额表：{len(reference)} 个模型")

    status = core.current_status(limits, usage, events, reference=reference)
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
