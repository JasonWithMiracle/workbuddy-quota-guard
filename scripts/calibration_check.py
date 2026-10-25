"""校准数据池的 CI 门禁。

检查项：
  1. 所有样本通过 schema 校验（含隐私扫描）；
  2. 聚合可正常执行，无内部错误；
  3. 若存在 ``calibration/reference.json``，它必须与当前样本集的聚合结果一致
     （即参考表是**最新**的，避免发版时参考表与样本脱节）；
  4. 样本中的 ``contributor.id`` 不得重复出现在同一模型下 —— 允许重复，
     但需在报告中可见（聚合时会被折叠）。

可本地运行：
    python scripts/calibration_check.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from wb_quota_guard import calibration  # noqa: E402

CAL_DIR = os.path.join(ROOT, "calibration")
SAMPLES_DIR = os.path.join(CAL_DIR, "samples")
REFERENCE = os.path.join(CAL_DIR, "reference.json")


def main():
    errors = []

    if not os.path.isdir(SAMPLES_DIR):
        print(f"校准样本目录不存在：{SAMPLES_DIR}")
        return 0

    samples, sample_errors = calibration.load_samples(SAMPLES_DIR)
    for err in sample_errors:
        errors.append(err)

    print(f"[1/4] 样本校验：{len(samples)} 条通过，{len(sample_errors)} 条失败")

    try:
        result = calibration.aggregate(samples)
    except Exception as exc:
        errors.append(f"聚合执行异常：{exc}")
        print(f"[2/4] 聚合失败：{exc}")
        return 1
    print(f"[2/4] 聚合完成：采纳 {result['adopted_count']} 个模型，"
          f"冲突 {len(result['conflicts'])} 个")

    for c in result["conflicts"]:
        print(f"      ⚠️ 冲突 {c['model']}@{c['window_hours']}h：{c['reason']}")

    # 3. 参考表一致性
    if os.path.isfile(REFERENCE):
        try:
            with open(REFERENCE, "r", encoding="utf-8") as fh:
                published = json.load(fh)
        except Exception as exc:
            errors.append(f"reference.json 解析失败：{exc}")
            published = None

        if published is not None:
            current = result["reference"]
            if published != current:
                p_keys = set(published or {})
                c_keys = set(current)
                only_pub = sorted(p_keys - c_keys)
                only_cur = sorted(c_keys - p_keys)
                changed = sorted(
                    k for k in (p_keys & c_keys)
                    if published[k] != current[k]
                )
                errors.append(
                    "reference.json 与当前样本集聚合结果不一致 —— "
                    f"请重新执行 `python -m wb_quota_guard calibrate publish` 并提交。"
                    + (f" 新增：{only_cur}" if only_cur else "")
                    + (f" 移除：{only_pub}" if only_pub else "")
                    + (f" 变更：{changed}" if changed else "")
                )
                print("[3/4] 参考表与样本集不一致 ❌")
            else:
                print("[3/4] 参考表与样本集一致 ✅")
        else:
            print("[3/4] 参考表解析失败 ❌")
    else:
        print("[3/4] 无 reference.json（首次贡献无需生成），跳过")

    # 4. 贡献者重复提示（不阻断，仅提示）
    by_model = {}
    for s in samples:
        by_model.setdefault((s["model"], s["window_hours"]), set()).add(
            s["contributor"]["id"])
    dups = {k: sorted(v) for k, v in by_model.items() if len(v) < len(
        [s for s in samples if (s["model"], s["window_hours"]) == k])}
    if dups:
        print(f"[4/4] 提示：{len(dups)} 个模型下存在同贡献者的多条样本"
              f"（聚合时会折叠为一条代表值）")
    else:
        print("[4/4] 无重复提交者样本")

    if errors:
        print("\n校准数据检查未通过：")
        for e in errors:
            print(f"  ✗ {e}")
        return 1

    print("\n校准数据检查通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
