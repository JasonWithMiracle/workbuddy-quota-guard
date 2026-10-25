"""生成月度校准数据发布的 Release 说明。

抽成独立脚本而非在 workflow 里内嵌 heredoc —— 后者会被 YAML 的缩进规则
破坏，且难以本地验证（沿用 hygiene_check.py 的同一原则：
**CI 逻辑超过 10 行就下沉为可本地运行的脚本**）。

用法：
    python scripts/gen_release_notes.py --reference calibration/reference.json \
        --out /tmp/notes.md [--date 2026-10-25]
"""
import argparse
import datetime
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def build_notes(reference, date_str):
    lines = [
        f"社区参考限额表更新（{date_str}）",
        "",
    ]
    if not reference:
        lines += [
            "本次无模型进入参考表（尚未满足「≥2 个独立贡献者且一致」的采纳条件）。",
            "",
            "本版本不含代码功能变更，仅更新数据。",
        ]
        return "\n".join(lines)

    lines += [
        f"本次纳入 **{len(reference)}** 个模型的交叉验证限额值。",
        "",
        "| 模型 | 窗口 | 限额 | 贡献者数 | 置信度 |",
        "|---|---|---|---|---|",
    ]
    for key, entry in sorted(reference.items()):
        lim = entry.get("limits", {}) or {}
        lim_s = " / ".join(
            f"{k}={v}" for k, v in sorted(lim.items()) if v is not None
        ) or "—"
        lines.append(
            f"| {entry.get('model', key)} | {entry.get('window_hours')}h | {lim_s} | "
            f"{entry.get('contributor_count')} | {entry.get('confidence')} |"
        )
    lines += [
        "",
        "> 采纳规则：同一模型同一窗口下，需 ≥2 个独立贡献者，",
        "> 剔除少数派离群者后各维度相对离散度在容差内。",
        "",
        "本版本不含代码功能变更，仅更新数据。",
    ]
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description="生成校准数据发布的 Release 说明")
    p.add_argument(
        "--reference",
        default=os.path.join(ROOT, "calibration", "reference.json"),
        help="参考限额表路径",
    )
    p.add_argument("--out", default=None, help="输出文件（默认输出到 stdout）")
    p.add_argument("--date", default=None, help="日期字符串（默认今天）")
    args = p.parse_args(argv)

    date_str = args.date or datetime.date.today().isoformat()
    reference = {}
    if os.path.isfile(args.reference):
        try:
            with open(args.reference, "r", encoding="utf-8") as fh:
                reference = json.load(fh)
        except Exception as exc:
            print(f"读取参考表失败：{exc}", file=sys.stderr)
            return 1

    notes = build_notes(reference, date_str)
    if args.out:
        with io.open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(notes)
        print(f"Release 说明已写出：{args.out}")
    else:
        print(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
