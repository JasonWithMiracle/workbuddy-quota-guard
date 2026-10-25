"""产物生成层：HTML 看板与 Markdown 交接文档。"""
import os
from datetime import datetime, timedelta, timezone

from .core import CST, ERROR_CODES

DASH_CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1b1f24;--muted:#5b6570;--line:#e3e7ec;
--red:#d93025;--amber:#e08c00;--green:#1a7f37;--blue:#1a56db}
@media (prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#171b21;--fg:#e6e9ee;
--muted:#98a2ad;--line:#262c34;--red:#ff6b60;--amber:#ffb020;--green:#4ade80;--blue:#7aa2ff}}
*{box-sizing:border-box}body{margin:0;padding:28px;background:var(--bg);color:var(--fg);
font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;line-height:1.6}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:26px 0 12px}
.sub{color:var(--muted);font-size:13px;margin-bottom:22px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin-bottom:8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.k{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.v{font-size:24px;font-weight:650;margin-top:6px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);
border-radius:10px;overflow:hidden;font-size:13px;margin-bottom:12px}
th{text-align:left;padding:10px 12px;background:color-mix(in srgb,var(--card) 92%,var(--line));
font-weight:600;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:9px 12px;border-bottom:1px solid var(--line);white-space:nowrap}
tr:last-child td{border-bottom:none}
.pill{display:inline-block;padding:2px 9px;border-radius:99px;font-size:11px;font-weight:600}
.high{background:color-mix(in srgb,var(--red) 18%,transparent);color:var(--red)}
.medium{background:color-mix(in srgb,var(--amber) 18%,transparent);color:var(--amber)}
.low{background:color-mix(in srgb,var(--green) 16%,transparent);color:var(--green)}
.unknown{background:color-mix(in srgb,var(--muted) 16%,transparent);color:var(--muted)}
.bar{height:7px;border-radius:99px;background:var(--line);overflow:hidden;min-width:90px}
.bar>i{display:block;height:100%;background:var(--blue)}
.note{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--blue);
border-radius:8px;padding:14px 16px;font-size:13px;margin:18px 0 20px}
code{background:color-mix(in srgb,var(--fg) 9%,transparent);padding:1px 5px;border-radius:4px;font-size:12px}
"""


def gen_handoff(limits, status, events, out_dir=None):
    """生成 429 断点交接文档。"""
    now = datetime.now(CST)
    lines = [
        "# 任务交接文档（Quota Guard 自动生成）",
        "",
        f"- 生成时间：{now.strftime('%Y-%m-%d %H:%M:%S')} UTC+8",
        "- 触发原因：模型调用触发 429 频率限额，任务被迫中断",
        f"- 统计窗口：滚动 {status.get('window_hours', 24)} 小时（按模型独立计算）",
        "",
        "## 一、当前各模型配额状态（务必先读）",
        "",
    ]
    models = status.get("models", [])
    if models:
        lines += ["| 模型 | 窗口内已用 Token | 窗口内已用 Credit | 请求数 | 该模型限额(下界) | 用量占比 | 风险 | 下次重置 |",
                  "|---|---|---|---|---|---|---|---|"]
        for w in models[:14]:
            lim = (f"{w['limit_tokens_M']}M" if w.get("limit_tokens_M")
                   else (f"{w['limit_credit']}cr" if w.get("limit_credit") else "未知"))
            ratio = f"{w['usage_ratio']*100:.0f}%" if w.get("usage_ratio") is not None else "未知"
            lines.append(f"| `{w['model']}` | {w['total_tokens']/1e6:.2f}M | {w['credit']} | "
                         f"{w['requests']} | {lim} | {ratio} | "
                         f"{ {'high':'RED HIGH','medium':'YELLOW MED','low':'GREEN LOW','unknown':'UNKNOWN'}[w['risk']] } | "
                         f"{w['next_reset']} |")
        lines += ["", "> 风险分级：>=80% 建议立即换模型；50-80% 需注意；<50% 安全。", ""]
    else:
        lines += ["_窗口内无活跃消耗记录。_", ""]
    lines += ["## 二、限额规则速查（供下个模型自律）", "",
              "| 错误码 | 含义 | 命中次数 |", "|---|---|---|"]
    for code, st in limits["code_stats"].items():
        lines.append(f"| {code} | {st['zh']} | {st['hits']} |")
    thr = limits.get("thresholds", {})
    lines += ["",
              f"- 全局 Token 限额中位估计：**{thr.get('token_limit_M')}M** / 窗口",
              f"- 全局 Credit 限额中位估计：**{thr.get('credit_limit')}** / 窗口",
              f"- 请求数中位估计：**{thr.get('request_limit')}** 次 / 窗口",
              f"- 滚动窗口长度：**{thr.get('window_hours_median')} 小时**",
              f"- 统计样本：{thr.get('sample_windows')} 个限流窗口",
              "",
              "## 三、各模型限额明细（账号 x 模型 独立配额）",
              "",
              "| 模型 | 429 次数 | 样本数 | 限额 Token(下界) | 限额 Credit(下界) | 请求数 |",
              "|---|---|---|---|---|---|"]
    pml = limits.get("per_model_limits", {})
    for m, v in sorted(pml.items(), key=lambda kv: -kv[1].get("hits_429", 0)):
        hits = limits.get("per_model_429", {}).get(m, 0)
        lines.append(f"| `{m}` | {hits} | {v.get('samples')} | "
                     f"{v.get('median_tokens_M')}M | {v.get('median_credit')} | {v.get('median_requests')} |")
    lines += ["", "## 四、切换模型建议", "",
              "按「历史 429 次数升序」排列，越靠前越安全（429 少 = 限额宽松或用量少）：", "",
              "| 模型 | 历史 429 次数 | 累计 credit | 累计 token |", "|---|---|---|---|"]
    rank = sorted(limits.get("per_model_429", {}).items(), key=lambda kv: kv[1])
    for m, n in rank:
        lines.append(f"| `{m}` | {n} | {limits.get('per_model_credit', {}).get(m, 0)} | "
                     f"{limits.get('per_model_tokens', {}).get(m, 0)/1e6:.1f}M |")
    lines += ["", "## 五、续跑清单", "",
              "1. 先读本文件「当前各模型配额状态」，确认目标模型不在高风险。",
              "2. 若目标模型为高风险：换用上表中 429 次数最低的模型，或等待其重置时刻到达。",
              "3. 断点续跑：复用原会话 ID（`--resume <session-id>`），上下文由本地 jsonl 完整保留，无需重建。",
              "4. 收尾前复核：`python wb_quota_guard.py status`。",
              "5. 若再次 429，重新生成 handoff 文档并交接。",
              "",
              "---",
              "*本文档由 `wb_quota_guard.py` 基于本地会话日志自动生成；限额为统计下界估计，需按实际表现校准。*",
              ""]
    out_dir = out_dir or os.getcwd()
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "handoff.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(chr(10).join(lines))
    return path


DASH_CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1b1f24;--muted:#5b6570;--line:#e3e7ec;
--red:#d93025;--amber:#e08c00;--green:#1a7f37;--blue:#1a56db}
@media (prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#171b21;--fg:#e6e9ee;
--muted:#98a2ad;--line:#262c34;--red:#ff6b60;--amber:#ffb020;--green:#4ade80;--blue:#7aa2ff}}
*{box-sizing:border-box}body{margin:0;padding:28px;background:var(--bg);color:var(--fg);
font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;line-height:1.6}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:26px 0 12px}
.sub{color:var(--muted);font-size:13px;margin-bottom:22px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin-bottom:8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.k{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.v{font-size:24px;font-weight:650;margin-top:6px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);
border-radius:10px;overflow:hidden;font-size:13px;margin-bottom:12px}
th{text-align:left;padding:10px 12px;background:color-mix(in srgb,var(--card) 92%,var(--line));
font-weight:600;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:9px 12px;border-bottom:1px solid var(--line);white-space:nowrap}
tr:last-child td{border-bottom:none}
.pill{display:inline-block;padding:2px 9px;border-radius:99px;font-size:11px;font-weight:600}
.high{background:color-mix(in srgb,var(--red) 18%,transparent);color:var(--red)}
.medium{background:color-mix(in srgb,var(--amber) 18%,transparent);color:var(--amber)}
.low{background:color-mix(in srgb,var(--green) 16%,transparent);color:var(--green)}
.unknown{background:color-mix(in srgb,var(--muted) 16%,transparent);color:var(--muted)}
.bar{height:7px;border-radius:99px;background:var(--line);overflow:hidden;min-width:90px}
.bar>i{display:block;height:100%;background:var(--blue)}
.note{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--blue);
border-radius:8px;padding:14px 16px;font-size:13px;margin:18px 0 20px}
code{background:color-mix(in srgb,var(--fg) 9%,transparent);padding:1px 5px;border-radius:4px;font-size:12px}
"""


def gen_dashboard(limits, status, out_dir=None):
    """生成 HTML 看板。"""
    tot = limits["totals"]
    thr = limits["thresholds"]
    cards = [
        ("累计请求", f"{tot['requests_with_usage']:,}"),
        ("累计 Token", f"{tot['total_tokens']/1e6:.0f}M"),
        ("累计 Credit", f"{tot['total_credit']:.0f}"),
        ("429 事件", f"{tot['events_429']}"),
        ("Token 限额(中位)", f"{thr.get('token_limit_M')}M"),
        ("Credit 限额(中位)", f"{thr.get('credit_limit')}"),
        ("滚动窗口", f"{thr.get('window_hours_median')}h"),
    ]
    card_html = "".join(
        '<div class="card"><div class="k">' + k + '</div><div class="v">' + v + "</div></div>"
        for k, v in cards)

    stat_rows = ""
    for w in status.get("models", [])[:16]:
        ratio = w.get("usage_ratio")
        pct = min(int((ratio or 0) * 100), 100)
        lim = (str(w["limit_tokens_M"]) + "M" if w.get("limit_tokens_M")
               else (str(w["limit_credit"]) + "cr" if w.get("limit_credit") else "-"))
        stat_rows += ("<tr><td><code>" + w["model"] + "</code></td>"
                      f"<td>{w['total_tokens']/1e6:.2f}M</td><td>{w['credit']}</td><td>{w['requests']}</td>"
                      "<td>" + lim + "</td>"
                      '<td><div class="bar"><i style="width:' + str(pct) + '%"></i></div></td>'
                      '<td><span class="pill ' + w["risk"] + '">' + w["risk"] + "</span></td></tr>")

    code_rows = "".join(
        "<tr><td><code>" + c + "</code></td><td>" + s["zh"] + "</td><td>" + s["category"]
        + "</td><td>" + str(s["hits"]) + "</td></tr>"
        for c, s in limits["code_stats"].items())

    model_rows = "".join(
        "<tr><td><code>" + m + "</code></td><td>" + str(n) + "</td>"
        "<td>" + str(limits["per_model_credit"].get(m, 0)) + "</td>"
        "<td>" + f"{limits['per_model_tokens'].get(m,0)/1e6:.0f}M</td></tr>"
        for m, n in sorted(limits["per_model_429"].items(), key=lambda kv: kv[1]))

    pml_rows = ""
    for m, v in sorted(limits.get("per_model_limits", {}).items(),
                       key=lambda kv: -limits.get("per_model_429", {}).get(kv[0], 0)):
        hits = limits.get("per_model_429", {}).get(m, 0)
        pml_rows += ("<tr><td><code>" + m + "</code></td><td>" + str(hits) + "</td><td>"
                     + str(v.get("samples")) + "</td><td>" + str(v.get("median_tokens_M")) + "M</td>"
                     "<td>" + str(v.get("median_credit")) + "</td>"
                     "<td>" + str(v.get("median_requests")) + "</td></tr>")

    win_rows = ""
    for w in limits["windows"]:
        if not w.get("requests"):
            continue
        zh = ERROR_CODES.get(w["code"], {}).get("zh", "")
        win_rows += ("<tr><td>" + w["code"] + "</td><td>" + zh + "</td><td>" + w["reset_at"]
                     + "</td><td>" + str(w["window_hours"]) + "h</td><td>" + str(w["credit"])
                     + "</td><td>" + f"{w['total_tokens']/1e6:.0f}M</td><td>" + str(w["requests"])
                     + "</td><td>" + str(w["hits"]) + "</td></tr>")

    html = ("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>WorkBuddy 配额看板</title><style>" + DASH_CSS + "</style></head><body>"
            "<h1>WorkBuddy 配额与限额看板</h1>"
            "<div class=\"sub\">数据源：本地会话日志（~/.workbuddy/projects）+ workbuddy.db ｜ 生成于 "
            + limits["generated_at"] + " ｜ 全程只读</div>"
            "<div class=\"grid\">" + card_html + "</div>"
            "<div class=\"note\"><b>怎么读：</b>限额粒度是「账号 × 模型」，每个模型有独立配额。"
            "表中「限额」是该模型历史上触发 429 时的滚动窗口消耗中位数，属<b>下界估计</b>；"
            "「用量占比」= 当前滚动窗口已用 / 该限额。占比 ≥80% 时应主动换模型或等待重置，"
            "避免任务中途被 429 打断。</div>"
            "<h2>当前滚动窗口用量（按模型）</h2>"
            "<table><thead><tr><th>模型</th><th>已用 Token</th><th>已用 Credit</th><th>请求数</th>"
            "<th>限额(下界)</th><th>用量占比</th><th>风险</th></tr></thead><tbody>"
            + (stat_rows or "<tr><td colspan=\"7\">窗口内无数据</td></tr>") + "</tbody></table>"
            "<h2>各模型限额明细</h2>"
            "<table><thead><tr><th>模型</th><th>429 次数</th><th>样本数</th><th>限额 Token</th>"
            "<th>限额 Credit</th><th>限额请求数</th></tr></thead><tbody>"
            + (pml_rows or "<tr><td colspan=\"6\">样本不足</td></tr>") + "</tbody></table>"
            "<h2>错误码目录（客户端内置分类）</h2>"
            "<table><thead><tr><th>错误码</th><th>含义</th><th>分类</th><th>本机命中</th></tr></thead><tbody>"
            + code_rows + "</tbody></table>"
            "<h2>各模型 429 历史</h2>"
            "<table><thead><tr><th>模型</th><th>429 次数</th><th>累计 Credit</th>"
            "<th>累计 Token</th></tr></thead><tbody>" + model_rows + "</tbody></table>"
            "<h2>限流窗口明细</h2>"
            "<table><thead><tr><th>错误码</th><th>含义</th><th>重置时刻</th><th>窗口长度</th>"
            "<th>窗口 Credit</th><th>窗口 Token</th><th>请求数</th><th>429 次数</th></tr></thead><tbody>"
            + win_rows + "</tbody></table></body></html>")
    out_dir = out_dir or os.getcwd()
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "quota_report.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return path
