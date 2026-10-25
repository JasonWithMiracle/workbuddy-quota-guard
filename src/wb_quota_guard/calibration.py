"""校准样本的校验与聚合。

设计目标：让「限额精度」从一个随手的估计，变成社区共享、可持续增量的数据集。

核心规则（对应用户决策「多贡献者一致才采纳」）：

  1. 单条样本不足以定论 —— 同一模型的样本必须来自 **≥2 个独立贡献者**；
  2. 这些样本之间必须 **一致** —— 相对离散度在容差内，否则判为冲突，不进聚合表；
  3. 人工锚点（manual-anchor）比自动反推（auto-inferred）权重更高，但仍需交叉验证；
  4. 冲突样本不丢弃，落到 conflicts 区并标注分歧来源，供后续人工研判。

术语说明：
  - 「独立贡献者」= contributor.id 不同。同一贡献者的多条样本不构成交叉验证。
  - 「一致」= 同一维度上，各样本相对中位数的偏差在 ``tolerance`` 之内。

本模块纯计算，不落盘；落盘职责在 cli.py。
"""

import json
import os
import re

SCHEMA_VERSION = 1

#: 聚合表采纳所需的最小独立贡献者数
MIN_CONTRIBUTORS = 2

#: 各维度的相对离散度容差（超出即判为冲突）
TOLERANCE = {
    "tokens_M": 0.25,
    "credit": 0.25,
    "requests": 0.25,
}

#: 样本里出现的所有数值维度
DIMENSIONS = ("tokens_M", "credit", "requests")

#: 人工锚点相对自动反推的权重倍数
ANCHOR_WEIGHT = 3.0
INFERRED_WEIGHT = 1.0

#: 离群判定倍数（相对偏差超过 容差 × 该倍数 才判为离群）
OUTLIER_FACTOR = 3.0


class SampleError(ValueError):
    """样本不满足 schema 或一致性要求。"""


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------

_CONFIDIENT_ID = re.compile(r"^[a-z0-9-]{3,40}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: 样本中绝对不允许出现的隐私痕迹（与 hygiene_check.py 口径一致）
_FORBIDDEN_PATTERNS = (
    (re.compile(r"[A-Za-z]:[\\/]Users[\\/]"), "Windows 用户目录绝对路径"),
    (re.compile(r"/Users/[A-Za-z0-9._-]+/"), "macOS 用户目录绝对路径"),
    (re.compile(r"/home/[A-Za-z0-9._-]+/"), "Linux home 绝对路径"),
    (re.compile(r"ObsidianCatalogue"), "本机 vault 路径"),
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I), "账号 UUID"),
    (re.compile(r"ghp_|github_pat_|sk-|AKIA"), "密钥字面量"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "邮箱地址"),
)


def _require(cond, msg):
    if not cond:
        raise SampleError(msg)


def _iter_strings(obj, path=""):
    """递归产出 ``(字段路径, 字符串值)``，用于逐值隐私扫描。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            child = f"{path}.{k}" if path else str(k)
            for item in _iter_strings(v, child):
                yield item
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            for item in _iter_strings(v, f"{path}[{i}]"):
                yield item
    elif isinstance(obj, str):
        yield path, obj


def validate_sample(sample, filename="<sample>"):
    """校验单条样本是否符合 schema。

    返回规范化后的样本副本；不合规则抛 ``SampleError``，消息可直接展示给提交者。
    """
    _require(isinstance(sample, dict), f"{filename}: 顶层必须是对象")

    allowed = {
        "schema_version", "contributor", "model", "observed_at",
        "window_hours", "limits", "method", "source",
    }
    unknown = set(sample) - allowed
    _require(not unknown, f"{filename}: 存在未知字段 {sorted(unknown)}")

    _require(sample.get("schema_version") == SCHEMA_VERSION,
             f"{filename}: schema_version 必须为 {SCHEMA_VERSION}")

    contributor = sample.get("contributor")
    _require(isinstance(contributor, dict), f"{filename}: 缺少 contributor")
    cid = contributor.get("id")
    _require(isinstance(cid, str) and _CONFIDIENT_ID.match(cid),
             f"{filename}: contributor.id 必须是 3-40 位小写字母/数字/连字符")
    kind = contributor.get("kind")
    _require(kind in ("human", "inferred"), f"{filename}: contributor.kind 必须是 human 或 inferred")

    model = sample.get("model")
    _require(isinstance(model, str) and 0 < len(model) <= 80,
             f"{filename}: model 必须是 1-80 字符的字符串")

    observed_at = sample.get("observed_at")
    _require(isinstance(observed_at, str) and _ISO_DATE.match(observed_at),
             f"{filename}: observed_at 必须是 YYYY-MM-DD 格式")

    window_hours = sample.get("window_hours")
    _require(isinstance(window_hours, (int, float)) and not isinstance(window_hours, bool)
             and 0 < window_hours <= 168,
             f"{filename}: window_hours 必须在 (0, 168] 区间")

    limits = sample.get("limits")
    _require(isinstance(limits, dict), f"{filename}: 缺少 limits")
    limit_keys = set(limits) - set(DIMENSIONS)
    _require(not limit_keys, f"{filename}: limits 含未知维度 {sorted(limit_keys)}")
    nonzero = False
    for k, v in limits.items():
        _require(isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0,
                 f"{filename}: limits.{k} 必须是非负数字")
        if v > 0:
            nonzero = True
    _require(nonzero, f"{filename}: limits 至少要有一项非零值")

    method = sample.get("method")
    _require(isinstance(method, dict), f"{filename}: 缺少 method")
    mkind = method.get("kind")
    _require(mkind in ("manual-anchor", "auto-inferred"),
             f"{filename}: method.kind 必须是 manual-anchor 或 auto-inferred")
    samples_n = method.get("samples")
    _require(isinstance(samples_n, int) and not isinstance(samples_n, bool) and 1 <= samples_n <= 100000,
             f"{filename}: method.samples 必须是 1-100000 的整数")
    notes = method.get("notes")
    if notes is not None:
        _require(isinstance(notes, str) and len(notes) <= 500,
                 f"{filename}: method.notes 最多 500 字符")

    source = sample.get("source")
    if source is not None:
        _require(isinstance(source, dict), f"{filename}: source 必须是对象")
        issue = source.get("issue")
        if issue is not None:
            _require(isinstance(issue, str) and issue.isdigit(),
                     f"{filename}: source.issue 必须是数字字符串")
        run_id = source.get("run_id")
        if run_id is not None:
            _require(isinstance(run_id, str) and len(run_id) <= 60,
                     f"{filename}: source.run_id 最多 60 字符")

    # 隐私扫描：逐个字符串值扫描，而不是扫 json.dumps 的结果
    # （json.dumps 会把反斜杠转义成 \\，导致 Windows 路径正则漏检）
    for path, text in _iter_strings(sample, ""):
        for pat, desc in _FORBIDDEN_PATTERNS:
            if pat.search(text):
                raise SampleError(f"{filename}: 字段 {path} 检测到{desc}，样本必须脱敏")

    # 规范化：数值统一转 float（requests 保持 int）
    norm = dict(sample)
    norm["limits"] = dict(limits)
    for k, v in limits.items():
        norm["limits"][k] = int(v) if k == "requests" else float(v)
    norm["window_hours"] = float(window_hours)
    return norm


def load_samples(samples_dir):
    """读取并校验目录下所有 ``*.json`` 样本。

    返回 ``(samples, errors)``：samples 为通过校验的样本列表，errors 为错误描述列表。
    单个坏样本不应阻断其余样本的处理（CI 需逐条报错）。
    """
    samples, errors = [], []
    if not os.path.isdir(samples_dir):
        return samples, errors
    for name in sorted(os.listdir(samples_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(samples_dir, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception as exc:
            errors.append(f"{name}: JSON 解析失败 — {exc}")
            continue
        try:
            samples.append(validate_sample(raw, name))
        except SampleError as exc:
            errors.append(str(exc))
    return samples, errors


# --------------------------------------------------------------------------
# 聚合
# --------------------------------------------------------------------------

def _median(vals):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else round((s[n // 2 - 1] + s[n // 2]) / 2, 2)


def _rel_spread(vals):
    """相对离散度：max-min 相对中位数的比值。中位数为 0 时视为无穷大。"""
    if not vals:
        return None
    med = _median(vals)
    if not med:
        return None
    return round((max(vals) - min(vals)) / med, 4)


def _weighted_median(values, weights):
    """加权中位数 —— 人工锚点权重更高，取值时更靠近锚点。

    实现思路：把每个样本的权重按「半单位」量化展开成一条虚拟序列，
    再取该序列的普通中位数。权重量化为奇数单位（×2 再 +1），
    使每个元素在序列中占据一个「中心 + 两侧各半格」的对称位置，
    因此等权情形下结果严格等于普通中位数（偶数个取均值）。
    """
    if not values:
        return None
    pairs = sorted(zip(values, weights))

    # 权重放大 200 倍（保证为整数）并 +1，使每个元素权重为奇数
    scaled = []
    for v, w in pairs:
        units = int(round(w * 200)) * 2 + 1
        scaled.extend([v] * units)

    n = len(scaled)
    if n == 0:
        return None
    if n % 2 == 1:
        return round(scaled[n // 2], 2)
    return round((scaled[n // 2 - 1] + scaled[n // 2]) / 2.0, 2)


def _confidence(contributor_count, spread, has_anchor):
    """置信度打分（0-1），用于聚合表里标注可信程度。

    规则：贡献者越多、离散度越小、含人工锚点则分数越高。
    """
    score = 0.0
    score += min(contributor_count / 4.0, 1.0) * 0.4
    score += (1.0 - min(spread if spread is not None else 1.0, 1.0)) * 0.3
    score += 0.3 if has_anchor else 0.0
    return round(min(score, 1.0), 2)


def _collapse_by_contributor(group):
    """把同一贡献者的多条样本折叠成一个代表值。

    目的：让「一致性判断」发生在**贡献者之间**，而不是样本之间。
    否则一个贡献者提交 10 条样本就能凭数量压过其他人的判断，
    而单个离群样本又能否决所有人 —— 两者都不利于社区池的健康。

    每位贡献者的代表值 = 其各维度样本的中位数（各维度独立折叠）。
    """
    by_c = {}
    for s in group:
        by_c.setdefault(s["contributor"]["id"], []).append(s)

    reps = []
    for cid, subs in by_c.items():
        rep = {
            "contributor": cid,
            "kind": "human" if any(x["contributor"]["kind"] == "human" for x in subs) else "inferred",
            "has_manual_anchor": any(x["method"]["kind"] == "manual-anchor" for x in subs),
            "samples": sum(x["method"]["samples"] for x in subs),
            "limits": {},
        }
        for dim in DIMENSIONS:
            vals = [x["limits"][dim] for x in subs if dim in x["limits"]]
            if vals:
                rep["limits"][dim] = _median(vals)
        reps.append(rep)
    return reps


def _drop_outliers(reps, dim):
    """剔除在某维度上明显离群的贡献者代表值。

    判据：以中位数为基准，相对偏差超过 ``OUTLIER_FACTOR`` 倍容差即判为离群。
    采用「多数一致」原则 —— 只有当**少数**派离群时才剔除，
    避免把真正的多数共识给剔掉。
    """
    tol = TOLERANCE[dim]
    vals = [(r["contributor"], r["limits"][dim]) for r in reps if dim in r["limits"]]
    if len(vals) < 3:
        return reps, []
    med = _median([v for _, v in vals])
    if not med:
        return reps, []

    outliers = []
    for cid, v in vals:
        if abs(v - med) / med > tol * OUTLIER_FACTOR:
            outliers.append(cid)

    if not outliers:
        return reps, []
    # 离群者超过一半 → 反而是少数派离群，放弃剔除，判为整体冲突
    if len(outliers) * 2 > len(vals):
        return reps, outliers

    kept = [r for r in reps if r["contributor"] not in outliers]
    return kept, outliers


def aggregate(samples):
    """把样本聚合为参考限额表。

    采纳规则：
      1. 同一模型同一窗口长度下，先把**每位贡献者的多条样本折叠成一个代表值**；
      2. 折叠后必须有 **≥2 个独立贡献者**；
      3. 对每个维度，先剔除少数派离群贡献者，再检查相对离散度是否在 ``TOLERANCE`` 内；
      4. 满足则采纳（人工锚点权重更高），否则落入 conflicts，不进参考表。

    为什么要「先折叠再判断」：如果直接拿样本做一致性判断，
    一个贡献者多提几条就能压过别人；而离群剔除又保证单个异常提交
    不会让整个模型对所有人不可用。
    """
    by_model = {}
    for s in samples:
        key = (s["model"], s["window_hours"])
        by_model.setdefault(key, []).append(s)

    reference, conflicts = {}, []

    for (model, window), group in sorted(by_model.items()):
        reps = _collapse_by_contributor(group)
        contributors = sorted(r["contributor"] for r in reps)
        n_contrib = len(contributors)

        if n_contrib < MIN_CONTRIBUTORS:
            conflicts.append({
                "model": model,
                "window_hours": window,
                "reason": f"独立贡献者仅 {n_contrib} 个（需 ≥{MIN_CONTRIBUTORS}）",
                "samples_seen": len(group),
                "contributors": contributors,
            })
            continue

        entry = {
            "model": model,
            "window_hours": window,
            "contributors": contributors,
            "contributor_count": n_contrib,
            "sample_count": len(group),
            "has_manual_anchor": any(r["has_manual_anchor"] for r in reps),
            "observed_at_range": [
                min(s["observed_at"] for s in group),
                max(s["observed_at"] for s in group),
            ],
            "limits": {},
            "confidence": None,
        }

        conflict_dims = []
        excluded = []
        for dim in DIMENSIONS:
            kept, outliers = _drop_outliers(reps, dim)
            if outliers:
                excluded.extend(outliers)
            vals = [r["limits"][dim] for r in kept if dim in r["limits"]]
            if not vals:
                continue
            weights = [
                (ANCHOR_WEIGHT if r["has_manual_anchor"] else INFERRED_WEIGHT)
                for r in kept if dim in r["limits"]
            ]
            spread = _rel_spread(vals)
            tol = TOLERANCE[dim]
            if spread is not None and spread > tol:
                conflict_dims.append({
                    "dimension": dim,
                    "spread": spread,
                    "tolerance": tol,
                    "values": vals,
                })
                continue
            entry["limits"][dim] = _weighted_median(vals, weights)

        if conflict_dims:
            conflicts.append({
                "model": model,
                "window_hours": window,
                "reason": "维度离散度超出容差",
                "samples_seen": len(group),
                "contributors": contributors,
                "excluded_contributors": sorted(set(excluded)),
                "dimensions": conflict_dims,
            })
            continue

        if not entry["limits"]:
            continue

        # contributors 字段反映**实际参与聚合**的贡献者（离群者已被剔除），
        # 避免读者误以为离群样本也被采信了
        excluded_set = set(excluded)
        if excluded_set:
            kept_reps = [r for r in reps if r["contributor"] not in excluded_set]
            entry["contributors"] = sorted(r["contributor"] for r in kept_reps)
            entry["contributor_count"] = len(entry["contributors"])

        spread = max(
            (_rel_spread([r["limits"][d] for r in reps if d in r["limits"]])
             for d in entry["limits"]),
            default=None,
        )
        entry["confidence"] = _confidence(n_contrib, spread, entry["has_manual_anchor"])
        entry["max_spread"] = spread
        if excluded:
            entry["excluded_contributors"] = sorted(set(excluded))
        reference[f"{model}@{window:g}h"] = entry

    return {
        "schema_version": SCHEMA_VERSION,
        "policy": {
            "min_contributors": MIN_CONTRIBUTORS,
            "tolerance": TOLERANCE,
            "outlier_factor": OUTLIER_FACTOR,
            "anchor_weight": ANCHOR_WEIGHT,
            "inferred_weight": INFERRED_WEIGHT,
            "rule": ("同一模型同一窗口下，先按贡献者折叠样本；需 ≥2 个独立贡献者；"
                     "剔除少数派离群者后各维度相对离散度在容差内，方可采纳"),
        },
        "adopted_count": len(reference),
        "reference": reference,
        "conflicts": conflicts,
    }


def merge_reference(local_limits, reference):
    """把聚合出的参考限额合并进本地反推结果。

    合并策略：
      - 参考表已采纳的模型 → **以参考值为准**（社区交叉验证过的更可信）；
      - 仅本地有样本的模型 → 保留本地反推值，标注来源为 ``local-inferred``；
      - 两者都有但参考表未采纳（冲突） → 保留本地值，标注 ``local-inferred (conflicted)``。

    返回 ``(merged_limits_dict, merge_report)``，不改原对象。
    """
    merged = {}
    report = {"from_reference": [], "from_local": [], "conflicted": []}

    local = (local_limits or {}).get("per_model_limits", {}) or {}
    ref = (reference or {}).get("reference", {}) or {}

    # 参考表优先。注意：参考表的主键是「模型@窗口h」，但消费方（current_status）
    # 是按纯模型名索引的，因此这里必须用 entry 里的 model 字段重新建键，
    # 否则同一个模型会被拆成「hy3@24h」和「hy3」两条。
    # 同一模型若有多个窗口版本，按贡献者数优先、置信度次之择优取一个。
    by_model = {}
    for key, entry in ref.items():
        model = entry.get("model") or key.split("@")[0]
        prev = by_model.get(model)
        if prev is None or (
            (entry.get("contributor_count") or 0),
            (entry.get("confidence") or 0),
        ) > (
            (prev.get("contributor_count") or 0),
            (prev.get("confidence") or 0),
        ):
            by_model[model] = entry

    for model, entry in by_model.items():
        merged[model] = {
            "source": "community-reference",
            "confidence": entry.get("confidence"),
            "contributor_count": entry.get("contributor_count"),
            "limits": entry.get("limits", {}),
            "samples": entry.get("sample_count"),
        }
        report["from_reference"].append(model)

    # 本地补充
    for model, entry in local.items():
        if model in merged:
            continue
        conflicted = any(
            c.get("model") == model for c in (reference or {}).get("conflicts", []) or []
        )
        merged[model] = {
            "source": "local-inferred (conflicted)" if conflicted else "local-inferred",
            "confidence": None,
            "contributor_count": 1,
            "limits": {
                "tokens_M": entry.get("median_tokens_M"),
                "credit": entry.get("median_credit"),
                "requests": entry.get("median_requests"),
            },
            "samples": entry.get("samples"),
        }
        (report["conflicted"] if conflicted else report["from_local"]).append(model)

    report["from_reference"].sort()
    report["from_local"].sort()
    report["conflicted"].sort()
    return merged, report
