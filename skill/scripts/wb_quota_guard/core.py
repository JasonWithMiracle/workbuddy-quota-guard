"""WorkBuddy 配额感知器 —— 核心分析层。

本模块只负责：扫描本地日志 -> 反推限额 -> 评估当前窗口状态。
不负责产出文件，产出见 :mod:`wb_quota_guard.report`。
"""
import collections
import glob
import json
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))

#: WorkBuddy 本地数据根目录，可用环境变量 ``WORKBUDDY_HOME`` 覆盖
HOME = os.path.expanduser("~")
WB = os.environ.get("WORKBUDDY_HOME") or os.path.join(HOME, ".workbuddy")
PROJECTS = os.path.join(WB, "projects")
DB = os.path.join(WB, "workbuddy.db")


#: 客户端业务错误码 -> 语义分类（逆向自 WorkBuddy/CodeBuddy 渲染层错误处理逻辑）
ERROR_CODES = {
    "6004":  {"category": "quota", "sub": "quota_token_limit",    "zh": "Token 频率限额（按窗口重置）"},
    "6000":  {"category": "quota", "sub": "quota_token_limit",    "zh": "Token 频率限额（按窗口重置）"},
    "6005":  {"category": "quota", "sub": "quota_request_limit",  "zh": "请求数频率限额"},
    "6006":  {"category": "quota", "sub": "quota_request_limit",  "zh": "请求数频率限额"},
    "6007":  {"category": "quota", "sub": "quota_request_limit",  "zh": "请求数频率限额"},
    "6008":  {"category": "quota", "sub": "quota_request_limit",  "zh": "请求数频率限额"},
    "14001": {"category": "quota", "sub": "quota_balance_exhausted", "zh": "余额耗尽"},
    "14002": {"category": "quota", "sub": "quota_balance_exhausted", "zh": "余额耗尽"},
    "14003": {"category": "quota", "sub": "quota_request_limit",  "zh": "请求数限额"},
    "14012": {"category": "quota", "sub": "quota_balance_exhausted", "zh": "余额耗尽"},
    "14013": {"category": "quota", "sub": "quota_balance_exhausted", "zh": "余额耗尽"},
    "14014": {"category": "quota", "sub": "quota_balance_exhausted", "zh": "余额耗尽"},
    "14018": {"category": "quota", "sub": "quota_balance_exhausted", "zh": "余额耗尽"},
    "10105": {"category": "quota", "sub": "quota_active_session",  "zh": "活跃会话超限"},
    "15001": {"category": "quota", "sub": "quota_web_search",     "zh": "联网搜索额度耗尽"},
    "6020":  {"category": "queue",  "sub": "queue_waiting",        "zh": "排队中（等待位）"},
    "6021":  {"category": "queue",  "sub": "queue_full",           "zh": "队列已满"},
    "6022":  {"category": "queue",  "sub": "queue_user_limit",     "zh": "用户并发超限"},
    "14016": {"category": "quota", "sub": "quota_not_activated",  "zh": "套餐未激活"},
    "14017": {"category": "quota", "sub": "quota_not_activated",  "zh": "套餐未激活"},
}


#: 从 429 错误文案中提取「窗口重置时刻」
RESET_RE = re.compile(
    r"将在\s*([0-9]{4}-[0-9]{2}-[0-9]{2}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})\s*UTC\+8\s*重置"
)


def parse_ts(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if v > 1e12:
        v /= 1000.0
    return v


def scan():
    """全量扫描本地会话日志。返回 usage 列表与 429 事件列表。"""
    usage, events = [], []
    files = glob.glob(os.path.join(PROJECTS, "*", "*.jsonl"))
    files += glob.glob(os.path.join(PROJECTS, "*", "*", "subagents", "*.jsonl"))
    for fp in files:
        try:
            fh = open(fp, encoding="utf-8", errors="ignore")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"providerData"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                pd = d.get("providerData") or {}
                ts = parse_ts(d.get("timestamp"))
                if ts is None:
                    continue
                ru = pd.get("rawUsage")
                if isinstance(ru, dict):
                    usage.append({
                        "ts": ts,
                        "model": pd.get("model") or pd.get("requestModelId"),
                        "credit": float(ru.get("credit") or 0),
                        "total": int(ru.get("total_tokens") or 0),
                        "prompt": int(ru.get("prompt_tokens") or 0),
                        "completion": int(ru.get("completion_tokens") or 0),
                        "cached": int(ru.get("prompt_cache_hit_tokens") or 0),
                        "session": d.get("sessionId"),
                    })
                err = pd.get("error")
                if isinstance(err, dict) and err.get("status") == 429:
                    msg = err.get("message") or ""
                    m = RESET_RE.search(msg)
                    events.append({
                        "ts": ts,
                        "model": pd.get("model") or pd.get("requestModelId"),
                        "code": str(err.get("code")),
                        "reset": m.group(1) if m else None,
                        "msg": msg,
                        "file": fp,
                        "session": d.get("sessionId"),
                    })
    usage.sort(key=lambda x: x["ts"])
    events.sort(key=lambda x: x["ts"])
    attribute_event_models(usage, events)
    return usage, events


def attribute_event_models(usage, events):
    """
    429 错误行本身不记录模型（providerData.model 为空）。
    用「同一会话内、时间上最接近该 429 的成功请求模型」反推归属模型。
    """
    by_session = collections.defaultdict(list)
    for u in usage:
        if u.get("model"):
            by_session[u.get("session")].append((u["ts"], u["model"]))
    for lst in by_session.values():
        lst.sort()
    for e in events:
        if e.get("model"):
            continue
        lst = by_session.get(e.get("session"))
        if not lst:
            continue
        # 取 429 之前 120 秒内最近的一次成功请求所用模型
        best, best_dt = None, None
        for ts, model in lst:
            if ts > e["ts"]:
                break
            dt = e["ts"] - ts
            if dt <= 120 and (best_dt is None or dt < best_dt):
                best, best_dt = model, dt
        if best:
            e["model"] = best
            e["model_source"] = "session_nearest"
        else:
            # 回退：取该会话内出现最多的模型
            cnt = collections.Counter(m for _, m in lst)
            if cnt:
                e["model"] = cnt.most_common(1)[0][0]
                e["model_source"] = "session_majority"


def build_windows(events):
    """按错误码 + reset 时刻还原限额窗口。"""
    by_code = collections.defaultdict(list)
    for e in events:
        if e.get("reset"):
            by_code[e["code"]].append(e["reset"])
    windows = []
    for code, resets in by_code.items():
        uniq = sorted(set(resets))
        for i, rs in enumerate(uniq):
            dtr = datetime.strptime(rs, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST).timestamp()
            if i > 0:
                prev = datetime.strptime(uniq[i - 1], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST).timestamp()
            else:
                prev = dtr - 24 * 3600
            n = sum(1 for e in events if e["code"] == code and e["reset"] == rs)
            windows.append({
                "code": code, "reset_at": rs, "window_start": prev, "window_end": dtr,
                "window_hours": round((dtr - prev) / 3600, 2), "hits": n,
            })
    windows.sort(key=lambda w: w["window_end"])
    return windows


def annotate(usage, windows):
    """给每个窗口填入窗口内的 credit / token / 请求数（含按模型拆分）。"""
    for w in windows:
        seg = [u for u in usage if w["window_start"] < u["ts"] <= w["window_end"]]
        w["credit"] = round(sum(u["credit"] for u in seg), 2)
        w["total_tokens"] = sum(u["total"] for u in seg)
        w["prompt_tokens"] = sum(u["prompt"] for u in seg)
        w["requests"] = len(seg)
        w["by_model"] = dict(collections.Counter(
            u["model"] for u in seg if u["model"]).most_common(8))
        # 每个模型在该窗口内的独立消耗（限流是「账号 × 模型」粒度）
        per = collections.defaultdict(lambda: {"credit": 0.0, "tokens": 0, "requests": 0})
        for u in seg:
            if not u["model"]:
                continue
            per[u["model"]]["credit"] += u["credit"]
            per[u["model"]]["tokens"] += u["total"]
            per[u["model"]]["requests"] += 1
        w["per_model_usage"] = {
            m: {"credit": round(v["credit"], 2), "tokens": v["tokens"], "requests": v["requests"]}
            for m, v in per.items()
        }
    return windows



def read_db_credits():
    """从 workbuddy.db 读取会话级 credit（需连 WAL 一并复制）。"""
    tmp = tempfile.mkdtemp(prefix="wbq_")
    try:
        for suffix in ("", "-wal", "-shm"):
            src = DB + suffix
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(tmp, "workbuddy.db" + suffix))
        conn = sqlite3.connect(os.path.join(tmp, "workbuddy.db"))
        rows = list(conn.execute(
            "select session_id, used, size, credit_json from session_usage "
            "where credit_json is not null and credit_json != '{}'"))
        out = []
        for sid, used, size, cj in rows:
            try:
                d = json.loads(cj)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            out.append({"session_id": sid, "used": used, "size": size,
                        "credit_map": d, "credit_total": round(sum(d.values()), 3)})
        conn.close()
        return out
    except Exception as exc:  # noqa: BLE001
        return [{"error": str(exc)}]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



def build_model_limits(usage, windows, events):
    """
    重建「账号 × 模型」的独立限额。

    语义（由上游 429 消息与本地实证共同确定）：
      - 429 错误在时刻 T 触发，声明窗口在 R 时刻重置；
      - 该模型被限流说明它在 [R-W, R] 这段滚动窗口内（仅看该模型自身的消耗）已超限，
        其中 W = 观察到的窗口长度中位数（≈24h）。
    因此：对该模型的每个触发事件，取 [R-W, R] 内「仅该模型」的消耗，作为限额的下界样本。
    """
    def median(vals):
        if not vals:
            return None
        s = sorted(vals)
        n = len(s)
        return s[n // 2] if n % 2 else round((s[n // 2 - 1] + s[n // 2]) / 2, 2)

    hours = [w["window_hours"] for w in windows if w["requests"] > 0]
    W = (median(hours) or 24.0) * 3600

    model_samples = collections.defaultdict(list)
    for e in events:
        m, rs = e.get("model"), e.get("reset")
        if not m or not rs or m == "(未记录)":
            continue
        r = datetime.strptime(rs, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST).timestamp()
        seg = [u for u in usage
               if u["model"] == m and (r - W) < u["ts"] <= r]
        if not seg:
            continue
        model_samples[m].append({
            "credit": round(sum(u["credit"] for u in seg), 2),
            "tokens": sum(u["total"] for u in seg),
            "requests": len(seg),
        })

    out = {}
    for m, samples in model_samples.items():
        out[m] = {
            "samples": len(samples),
            "median_credit": median([s["credit"] for s in samples]),
            "median_tokens_M": round(median([s["tokens"] for s in samples]) / 1e6, 2),
            "median_requests": median([s["requests"] for s in samples]),
            "max_credit": round(max(s["credit"] for s in samples), 2),
            "max_tokens_M": round(max(s["tokens"] for s in samples) / 1e6, 2),
        }
    return out, W / 3600


def analyze():
    usage, events = scan()
    windows = annotate(usage, build_windows(events))
    code_stats = collections.Counter(e["code"] for e in events)
    per_model = collections.Counter()
    for e in events:
        per_model[e["model"] or "(未记录)"] += 1
    model_credit = collections.defaultdict(float)
    model_tokens = collections.defaultdict(int)
    for u in usage:
        if u["model"]:
            model_credit[u["model"]] += u["credit"]
            model_tokens[u["model"]] += u["total"]

    # 限流窗口的阈值估计：用中位数而非均值，避免被异常窗口拉偏
    tok_windows = [w for w in windows if w["code"] in ("6004", "6000") and w["requests"] > 0]
    credit_windows = [w for w in windows if w["code"] in ("6004", "6000") and w["requests"] > 0]

    def median(vals):
        if not vals:
            return None
        s = sorted(vals)
        n = len(s)
        return s[n // 2] if n % 2 else round((s[n // 2 - 1] + s[n // 2]) / 2, 2)

    limits = {
        "generated_at": datetime.now(CST).isoformat(),
        "error_code_catalog": ERROR_CODES,
        "code_stats": {k: {"hits": v, "zh": ERROR_CODES.get(k, {}).get("zh", "未知"),
                           "category": ERROR_CODES.get(k, {}).get("category", "unknown")}
                       for k, v in code_stats.most_common()},
        "windows": [{k: v for k, v in w.items() if k not in ("window_start", "window_end")}
                    for w in windows],
        "thresholds": {
            "token_limit_M": median([w["total_tokens"] for w in tok_windows]) and
                            round(median([w["total_tokens"] for w in tok_windows]) / 1e6, 2),
            "prompt_token_M": round(median([w["prompt_tokens"] for w in tok_windows]) / 1e6, 2),
            "credit_limit": median([w["credit"] for w in credit_windows]),
            "request_limit": median([w["requests"] for w in tok_windows]),
            "window_hours_median": median([w["window_hours"] for w in tok_windows]),
            "sample_windows": len(tok_windows),
            "method": "对所有 code∈{6000,6004} 的限流窗口取中位数；窗口边界由错误消息中的 reset 时刻重建",
        },
        "per_model_429": dict(per_model.most_common()),
        "per_model_credit": {k: round(v, 2) for k, v in sorted(model_credit.items(), key=lambda kv: -kv[1])},
        "per_model_tokens": dict(sorted(model_tokens.items(), key=lambda kv: -kv[1])),
        "totals": {
            "requests_with_usage": len(usage),
            "total_credit": round(sum(u["credit"] for u in usage), 2),
            "total_tokens": sum(u["total"] for u in usage),
            "events_429": len(events),
            "files_scanned": len(glob.glob(os.path.join(PROJECTS, "*", "*.jsonl"))),
        },
    }
    # 按模型重建独立限额（滚动窗口下界样本）
    model_limits, W_hours = build_model_limits(usage, windows, events)
    limits["thresholds"]["window_hours_median"] = W_hours
    limits["per_model_limits"] = model_limits
    limits["per_model_limits_note"] = (
        "每个模型的限额由「该模型触发 429 时的滚动窗口内自身消耗」取中位数得到，"
        "属限额下界估计；样本数少的模型可信度低，需实际触发后校准。")

    return limits, usage, events


def current_status(limits, usage, events, reference=None):
    """
    按「账号 × 模型」估算当前滚动窗口用量。

    窗口语义：滚动 W 小时（W = 观察到的窗口长度中位数 ≈ 24h）。
    每个模型独立计算自己在过去 W 小时内的消耗，与该模型的历史限额下界对比。

    ``reference`` 为可选的社区校准参考表（``{模型名: {"limits": {...}, "confidence": ...}}``）。
    提供时以参考值优先（社区多贡献者交叉验证过的更可信），否则回落到本地反推值。
    """
    now = datetime.now(CST).timestamp()
    W = (limits.get("thresholds", {}).get("window_hours_median") or 24.0) * 3600
    per_model_limits = limits.get("per_model_limits", {})
    models_seen = {u["model"] for u in usage if u["model"]}

    rows = []
    for m in sorted(models_seen):
        seg = [u for u in usage if u["model"] == m and now - W < u["ts"] <= now]
        if not seg:
            continue
        credit = sum(u["credit"] for u in seg)
        tokens = sum(u["total"] for u in seg)
        reqs = len(seg)
        lim = per_model_limits.get(m, {})
        lim_tok = lim.get("median_tokens_M")
        lim_cred = lim.get("median_credit")
        limit_source = "local-inferred"
        confidence = None

        # 社区校准参考值优先
        ref = (reference or {}).get(m)
        if ref:
            rl = ref.get("limits", {})
            if rl.get("tokens_M"):
                lim_tok = rl["tokens_M"]
                limit_source = "community-reference"
            elif rl.get("credit"):
                lim_cred = rl["credit"]
                limit_source = "community-reference"
            confidence = ref.get("confidence")

        ratio = None
        risk = "unknown"
        if lim_tok:
            ratio = tokens / (lim_tok * 1e6)
            risk = "high" if ratio >= 0.8 else "medium" if ratio >= 0.5 else "low"
        elif lim_cred and credit:
            ratio = credit / lim_cred
            risk = "high" if ratio >= 0.8 else "medium" if ratio >= 0.5 else "low"
        # 该模型最近一次 429 声明的重置时刻
        nxt = None
        for e in reversed(events):
            if e.get("model") == m and e.get("reset"):
                r = datetime.strptime(e["reset"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST).timestamp()
                if r > now:
                    nxt = e["reset"]
                    break
        rows.append({
            "model": m,
            "window_hours": round(W / 3600, 1),
            "credit": round(credit, 2),
            "total_tokens": tokens,
            "requests": reqs,
            "limit_tokens_M": lim_tok,
            "limit_credit": lim_cred,
            "usage_ratio": round(ratio, 3) if ratio is not None else None,
            "risk": risk,
            "next_reset": nxt or "未知",
            "hits_429": limits.get("per_model_429", {}).get(m, 0),
            "limit_source": limit_source,
            "confidence": confidence,
        })
    rows.sort(key=lambda r: (r["usage_ratio"] is None, -(r["usage_ratio"] or 0)))
    status = {
        "now": datetime.now(CST).isoformat(),
        "window_hours": round(W / 3600, 1),
        "window_semantics": "滚动窗口：统计过去 W 小时内该模型自身消耗；限额为该模型历史触发 429 时的窗口下界",
        "models": rows,
    }
    return status
