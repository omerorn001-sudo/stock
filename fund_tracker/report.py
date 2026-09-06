"""Self-contained HTML plus machine-readable exports; untrusted text is escaped."""
from __future__ import annotations

import csv
import html
import json
from pathlib import Path

from .core import PERIODS
from .providers import dump


def esc(value):
    return html.escape(str(value)) if value is not None else "—"


def num(value, suffix=""):
    return f"{value:,.2f}{suffix}" if value is not None else "—"


def rank(fund, metric):
    info = fund["ranks"][metric]
    return f"{info['rank']}/{info['total']}" if info["rank"] is not None else "—"


def csv_write(path, rows, columns):
    def safe(v):
        if isinstance(v, (list, dict)):
            v = json.dumps(v, ensure_ascii=False)
        if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
            return "'" + v
        return v
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({k: safe(r.get(k)) for k in columns} for r in rows)


def render(report):
    meta, funds = report["metadata"], report["funds"]
    is_demo = meta.get("demo", False)
    title = f"基金一年收益 TOP {len(funds)}"
    banner = "演示数据 · 全部为测试样例，非真实基金榜单" if is_demo else "公开数据观察 · 非投资建议"
    lines = []
    sections = []
    for f in funds:
        p, info, analysis = f["profile"], f["holdings_info"], f["analysis"]
        row = [f'<a href="#fund-{f["code"]}">{esc(f["name"])}</a><small>{f["code"]}</small>',
               esc(p.get("manager")), esc(p.get("inception_date")),
               f'{esc(p.get("aum_raw"))}<small>规模日期 {esc(p.get("aum_as_of"))}</small>',
               f'{num(f["nav"])}<small>{rank(f, "nav")}</small>']
        for metric in PERIODS:
            row.append(f'{num(f[metric], "%")}<small>{rank(f, metric)}</small>')
        row += [f'{num(f["1d"], "%")}<small>{rank(f, "1d")}</small>', esc(f["nav_date"])]
        lines.append(f'<tr data-code="{f["code"]}">' + "".join(f"<td>{x}</td>" for x in row) + "</tr>")
        holding_rows = []
        for h in f["holdings"]:
            values = [h["stock_code"], h["stock_name"], h["market"], num(h["weight_pct"], "%"),
                      h.get("industry") or "未分类", "、".join(h.get("concepts", [])) or "未映射 / 未知",
                      num(h.get("shares_10k")), num(h.get("value_10k_reported"))]
            holding_rows.append("<tr>" + "".join(f"<td>{esc(v)}</td>" for v in values) + "</tr>")
        industries = "；".join(f'{x["name"]} {x["weight_pct_of_fund_nav"]:.2f}%' for x in analysis["industries"][:5]) or "无法计算"
        concepts = "；".join(f'{x["name"]} {x["weight_pct_of_fund_nav"]:.2f}%' for x in analysis["concepts"][:8]) or "无法计算"
        sources = "https://fundf10.eastmoney.com/ccmx_" + f["code"] + ".html"
        sections.append(f'''<article class="fund-detail" id="fund-{f['code']}">
<h3>{esc(f['name'])} <span>{f['code']}</span></h3>
<p class="muted">{esc(p.get('fund_type'))} · 持仓报告期 {esc(info.get('report_date'))} · 报告距采集日 {esc(info.get('report_age_days'))} 天</p>
<p><b>已获股票权重</b> {num(analysis['disclosed_weight_pct'], '%')}　<b>行业映射覆盖率</b> {num(analysis['industry_coverage_of_disclosed_pct'], '%')}　<b>概念映射覆盖率</b> {num(analysis['concept_coverage_of_disclosed_pct'], '%')}</p>
<p><b>行业（占基金净值）</b> {esc(industries)}</p><p><b>概念（可重叠）</b> {esc(concepts)}</p>
<p class="notice">{esc(info.get('reason') or info.get('scope'))}{'；明细可能被源站截断' if info.get('possibly_truncated') else ''}</p>
<details><summary>查看 {len(f['holdings'])} 条已披露股票持仓</summary>
<div class="table-wrap" tabindex="0" role="region" aria-label="持仓明细表"><table class="holdings"><thead><tr><th>代码</th><th>股票</th><th>市场</th><th>占净值</th><th>行业</th><th>概念</th><th>万股</th><th>市值（来源万元）</th></tr></thead><tbody>{''.join(holding_rows) or '<tr><td colspan="8">未取得数据；这不代表没有股票持仓。</td></tr>'}</tbody></table></div>
<p><a href="{sources}" target="_blank" rel="noopener noreferrer">查看持仓来源页面 ↗</a> · 完整分类来源、时间与失败原因见 JSON。</p></details></article>''')
    headers = ["基金 / 代码", "基金经理", "成立时间", "披露资产规模", "单位净值 / 排名", "近一年 / 排名", "近半年 / 排名", "近三月 / 排名", "近一月 / 排名", "近一周 / 排名", "上一交易日 / 排名", "净值日期"]
    warnings = report.get("warnings", [])
    issues = "".join(f"<li>{esc(w['source'])}：{esc(w['error'])}</li>" for w in warnings[:60])
    template = Path(__file__).with_name("template.html").read_text(encoding="utf-8")
    tokens = {
        "TITLE": esc(title), "BANNER": esc(banner), "STATUS": esc(meta["status"]),
        "ASOF": esc(meta["collected_at"]), "CATEGORY": esc(meta["category"]),
        "UNIVERSE": str(meta["rank_universe"]), "ELIGIBLE": str(meta["one_year_eligible"]),
        "PREVIOUS": esc(meta.get("previous_trading_day")), "COUNT": str(len(funds)),
        "HEADERS": "".join(f"<th scope=\"col\">{h}</th>" for h in headers), "ROWS": "".join(lines),
        "DETAILS": "".join(sections), "ISSUES": issues or "<li>无采集异常记录。</li>",
        "WARNCOUNT": str(len(warnings)),
        "CONCEPT_STATUS": esc(f"{meta['concept_coverage'].get('boards_completed', 0)}/{meta['concept_coverage'].get('boards_total', 0)} 个板块已读取；完整性 {meta['concept_coverage'].get('complete', False)}")}
    for key, value in tokens.items():
        template = template.replace("@@" + key + "@@", value)
    return template


def write_reports(directory: Path, report: dict):
    directory.mkdir(parents=True, exist_ok=True)
    dump(directory / "funds.json", report)
    dump(directory / "sources.json", report["sources"])
    flat = []
    holdings = []
    allocations = []
    for f in report["funds"]:
        row = {"基金代码": f["code"], "基金名称": f["name"], "净值日期": f["nav_date"],
               "基金经理": f["profile"].get("manager"), "成立时间": f["profile"].get("inception_date"),
               "基金类型": f["profile"].get("fund_type"), "披露资产规模": f["profile"].get("aum_raw"),
               "规模日期": f["profile"].get("aum_as_of"), "单位净值": f["nav"], "净值排名": rank(f, "nav"),
               "持仓报告期": f["holdings_info"].get("report_date")}
        for metric, label in {**PERIODS, "1d": "上一交易日"}.items():
            row[label + "收益率%"] = f[metric]
            row[label + "排名"] = rank(f, metric)
        flat.append(row)
        holdings.extend({"fund_code": f["code"], "fund_name": f["name"], **h} for h in f["holdings"])
        for kind in ["industries", "concepts"]:
            allocations.extend({"fund_code": f["code"], "kind": kind, **a} for a in f["analysis"][kind])
    csv_write(directory / "top50.csv", flat, list(flat[0]))
    csv_write(directory / "holdings.csv", holdings, ["fund_code", "fund_name", "stock_code", "stock_name", "market", "report_date", "weight_pct", "shares_10k", "value_10k_reported", "industry", "concepts", "market_tags", "holdings_source_id", "industry_source_id", "concept_source_ids", "classification_status"])
    csv_write(directory / "allocations.csv", allocations, ["fund_code", "kind", "name", "weight_pct_of_fund_nav"])
    (directory / "index.html").write_text(render(report), encoding="utf-8")
    m = report["metadata"]
    summary = f"## 基金 TOP {len(flat)}\n\n- 采集时间：{m['collected_at']}\n- 状态：{m['status']}\n- 榜单池：{m['rank_universe']}；近一年有效：{m['one_year_eligible']}\n- 上一交易日：{m.get('previous_trading_day') or '无法确认'}\n- 采集异常/回退记录：{len(report['warnings'])}\n\n持仓为定期披露，不是实时全仓。跨净值日期、跨基金类型排名并不等于同类可比业绩。\n"
    (directory / "summary.md").write_text(summary, encoding="utf-8")
