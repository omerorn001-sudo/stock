"""HTML 报告渲染（无模板引擎依赖）。

诚实性约定（与仓库现有报告一致）：不可得的数据一律显示 ``—``，
并在“数据源与缺失说明”里列出原因，绝不编造数值。
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Mapping, Sequence

MISSING = "—"

CSS = "\n".join(
    [
        ":root { --up:#d92b2b; --down:#12a05c; --ink:#222; --muted:#767676; --line:#e8e8e8; --bg:#f7f7f8; }",
        "* { box-sizing:border-box; }",
        "body { margin:0; padding:24px 18px 60px; background:var(--bg); color:var(--ink);",
        "  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif; }",
        ".wrap { max-width:1120px; margin:0 auto; }",
        "h1 { font-size:23px; margin:0 0 6px; }",
        "h2 { font-size:18px; margin:34px 0 10px; padding-left:9px; border-left:4px solid var(--up); }",
        "h3 { font-size:15px; margin:20px 0 8px; }",
        "h4 { font-size:13px; margin:14px 0 6px; color:var(--muted); font-weight:600; }",
        ".sub { color:var(--muted); font-size:12px; line-height:1.7; }",
        ".card { background:#fff; border:1px solid var(--line); border-radius:8px; padding:14px 16px; margin:12px 0; }",
        ".grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(168px,1fr)); gap:10px; margin:10px 0; }",
        ".kv { background:#fafafa; border:1px solid var(--line); border-radius:6px; padding:8px 10px; }",
        ".kv .k { color:var(--muted); font-size:11px; }",
        ".kv .v { font-size:15px; margin-top:3px; font-variant-numeric:tabular-nums; }",
        "table { width:100%; border-collapse:collapse; font-size:12.5px; margin:8px 0; }",
        "th,td { border-bottom:1px solid var(--line); padding:6px 8px; text-align:right; white-space:nowrap; }",
        "th { background:#fafafa; color:var(--muted); font-weight:600; }",
        "th:first-child,td:first-child,td.txt,th.txt { text-align:left; white-space:normal; }",
        ".up { color:var(--up); }",
        ".down { color:var(--down); }",
        ".tag { display:inline-block; font-size:11px; padding:1px 7px; border-radius:10px; background:#f0f0f0;",
        "  color:var(--muted); margin-right:5px; }",
        ".note { font-size:11.5px; color:var(--muted); margin:5px 0 0; }",
        ".miss { color:var(--muted); }",
        "svg { display:block; max-width:100%; }",
        "details { margin:6px 0; }",
        "summary { cursor:pointer; font-size:13px; color:#2f6fd0; }",
        "ul.news { margin:6px 0 0 16px; padding:0; font-size:12.5px; line-height:1.85; }",
        "a { color:#2f6fd0; text-decoration:none; }",
        "a:hover { text-decoration:underline; }",
        "footer { margin-top:36px; font-size:11.5px; color:var(--muted); line-height:1.8; }",
    ]
)


def esc(value: Any) -> str:
    if value is None:
        return MISSING
    text = str(value).strip()
    return html.escape(text) if text else MISSING


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value


def fmt_num(value: Any, digits: int = 2, suffix: str = "") -> str:
    if not _is_number(value):
        return MISSING
    return f"{value:,.{digits}f}{suffix}"


def fmt_pct(value: Any, digits: int = 2) -> str:
    """涨跌幅百分数，红涨绿跌。"""
    if not _is_number(value):
        return f'<span class="miss">{MISSING}</span>'
    cls = "up" if value > 0 else ("down" if value < 0 else "")
    sign = "+" if value > 0 else ""
    return f'<span class="{cls}">{sign}{value:,.{digits}f}%</span>'


def fmt_money(value: Any, unit: str = "亿元", divisor: float = 1e8, digits: int = 2) -> str:
    """元 -> 亿元。"""
    if not _is_number(value):
        return MISSING
    return f"{value / divisor:,.{digits}f}{unit}"


def fmt_shares(value: Any) -> str:
    """股 -> 万股。"""
    if not _is_number(value):
        return MISSING
    return f"{value / 1e4:,.2f}万股"


def _kv(label: str, value: str) -> str:
    return f'<div class="kv"><div class="k">{esc(label)}</div><div class="v">{value}</div></div>'


def _note(text: Any) -> str:
    if not text:
        return ""
    if isinstance(text, (list, tuple)):
        items = [str(t) for t in text if t]
        if not items:
            return ""
        return f'<p class="note">说明：{esc(" ｜ ".join(items))}</p>'
    return f'<p class="note">说明：{esc(text)}</p>'


def _source_tag(source: Any) -> str:
    return f'<span class="tag">数据源 {esc(source)}</span>' if source else ""


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]], text_columns: Sequence[int] = (0,)) -> str:
    if not rows:
        return f'<p class="note">{MISSING} 无可用行</p>'
    head = "".join(
        f'<th class="txt">{esc(h)}</th>' if i in text_columns else f"<th>{esc(h)}</th>"
        for i, h in enumerate(headers)
    )
    body = []
    for row in rows:
        cells = "".join(
            f'<td class="txt">{cell}</td>' if i in text_columns else f"<td>{cell}</td>"
            for i, cell in enumerate(row)
        )
        body.append(f"<tr>{cells}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _link(title: Any, url: Any) -> str:
    if url:
        return f'<a href="{html.escape(str(url), quote=True)}" target="_blank" rel="noopener">{esc(title)}</a>'
    return esc(title)


# ---------------- 各章节 ----------------
def _section_indexes(payload: Mapping[str, Any]) -> str:
    items = payload.get("indexes") or []
    parts = ['<h2 id="index">1. 指数近一年行情</h2>']
    compare = payload.get("index_compare_svg")
    if compare:
        parts.append(
            '<div class="card"><h3>主要宽基相对走势（首日=100）</h3>' + compare + "</div>"
        )
    if not items:
        parts.append(f'<div class="card"><p class="note">{MISSING} 未采集到指数行情</p></div>')
        return "".join(parts)

    rows = [
        [
            f"{esc(it.get('name'))} <span class=\"sub\">{esc(it.get('code'))}</span>",
            fmt_num(it.get("last_close")),
            fmt_pct(it.get("pct_chg")),
            fmt_num(it.get("high")),
            fmt_num(it.get("low")),
            esc(it.get("rows")),
            esc(it.get("source")),
        ]
        for it in items
    ]
    parts.append(
        '<div class="card"><h3>区间汇总</h3>'
        + _table(
            ["指数", "最新收盘", "区间涨跌", "区间最高", "区间最低", "交易日数", "数据源"],
            rows,
            text_columns=(0, 6),
        )
        + "</div>"
    )
    for it in items:
        parts.append('<div class="card">')
        parts.append(
            f"<h3>{esc(it.get('name'))}（{esc(it.get('code'))}） {_source_tag(it.get('source'))}</h3>"
        )
        parts.append(it.get("line_svg") or "")
        if it.get("candle_svg"):
            parts.append('<h4>日 K 线</h4>' + it["candle_svg"])
        parts.append(_note(it.get("note")))
        parts.append("</div>")
    return "".join(parts)


def _stock_href(code: Any) -> str:
    return f"stocks/{html.escape(str(code or ''), quote=True)}.html"


def _stock_cell(code: Any, name: Any) -> str:
    return f'<a href="{_stock_href(code)}">{esc(name)}</a> <span class="sub">{esc(code)}</span>'


def _section_new_stocks(payload: Mapping[str, Any]) -> str:
    """新股总览：一行一只，明细拆到 stocks/<代码>.html。"""
    items = payload.get("new_stocks") or []
    parts = ['<h2 id="new">2. 新股总览（上市至今行情）</h2>']
    if not items:
        parts.append(f'<div class="card"><p class="note">{MISSING} 未采集到新股数据</p></div>')
        return "".join(parts)

    rows = []
    for it in items:
        since = it.get("since_ipo") or {}
        first_days = it.get("first_days") or []
        with_intraday = sum(1 for day in first_days if (day.get("intraday") or {}).get("granularity"))
        rows.append(
            [
                _stock_cell(it.get("code"), it.get("name")),
                esc(it.get("list_date")),
                fmt_num(since.get("first_close")),
                fmt_num(since.get("last_close")),
                fmt_pct(since.get("pct_chg")),
                esc(since.get("rows")),
                f"{len(first_days)} / {with_intraday}",
                esc(it.get("source")),
            ]
        )
    parts.append(
        f'<div class="card"><h3>近一年上市新股 {len(items)} 只</h3>'
        + _table(
            ["股票", "上市日期", "首日收盘", "最新收盘", "上市以来涨跌", "交易日数", "首N日/其中有分时", "数据源"],
            rows,
            text_columns=(0, 1, 7),
        )
        + '<p class="note">点击股票名进入明细页（stocks/&lt;代码&gt;.html）：上市至今收盘线与日 K、'
        "上市初期逐日行情与分时图、十大流通股东、主营业务、估值走势。"
        "「首N日/其中有分时」= 已采集的上市初期交易日数 / 其中成功取到分时的天数；"
        "分时接口只保留近期数据，较早上市的新股取不到，按约定标 — 而不编造。</p></div>"
    )
    return "".join(parts)


def _section_profiles(payload: Mapping[str, Any]) -> str:
    """个股画像总览：一行一只，字段明细见各股明细页。"""
    items = payload.get("profiles") or []
    parts = ['<h2 id="profile">3. 个股画像总览（市值 / 估值 / 换手 / 板块）</h2>']
    if not items:
        parts.append(f'<div class="card"><p class="note">{MISSING} 未采集到个股画像</p></div>')
        return "".join(parts)

    rows = [
        [
            _stock_cell(it.get("code"), it.get("name")),
            esc(it.get("board")),
            esc(it.get("industry")),
            fmt_num(it.get("price")),
            fmt_pct(it.get("pct_chg")),
            fmt_money(it.get("total_mv")),
            fmt_money(it.get("float_mv")),
            fmt_num(it.get("pe_static")),
            fmt_num(it.get("pe_dynamic")),
            fmt_num(it.get("pe_ttm")),
            fmt_num(it.get("turnover"), 2, "%"),
            esc(len((it.get("holders") or {}).get("rows") or []) or MISSING),
        ]
        for it in items
    ]
    parts.append(
        f'<div class="card"><h3>汇总表（{len(items)} 只）</h3>'
        + _table(
            [
                "股票",
                "所属板块",
                "所属行业",
                "最新价",
                "涨跌幅",
                "总市值",
                "流通市值",
                "市盈率(静)",
                "市盈率(动)",
                "市盈率(TTM)",
                "换手率",
                "股东行数",
            ],
            rows,
            text_columns=(0, 1, 2),
        )
        + '<p class="note">口径：市盈率(静)=LYR（上一完整年度）；市盈率(动)=东财“动态市盈率”（当期年化推算）；'
        "TTM=最近四个季度滚动。三者不可直接比较；亏损股可能为空或负值。"
        "「股东行数」为已取到的十大流通股东行数；股东明细、主营业务与估值走势见各股明细页。</p></div>"
    )
    return "".join(parts)


def _section_sectors(payload: Mapping[str, Any]) -> str:
    items = payload.get("sectors") or []
    parts = ['<h2 id="sector">4. 所属板块行情</h2>']
    if not items:
        parts.append(f'<div class="card"><p class="note">{MISSING} 未采集到板块行情</p></div>')
        return "".join(parts)
    if payload.get("sector_compare_svg"):
        parts.append('<div class="card"><h3>板块相对走势（首日=100）</h3>' + payload["sector_compare_svg"] + "</div>")
    for it in items:
        parts.append('<div class="card">')
        parts.append(
            f"<h3>{esc(it.get('name'))} {esc(it.get('code') or '')} {_source_tag(it.get('source'))}</h3>"
        )
        parts.append(
            '<div class="grid">'
            + _kv("区间涨跌", fmt_pct(it.get("pct_chg")))
            + _kv("最新指数", fmt_num(it.get("last_close")))
            + _kv("采集交易日数", esc(it.get("rows")))
            + _kv("本次关注成分股", esc(len(it.get("members") or [])))
            + "</div>"
        )
        parts.append(it.get("line_svg") or "")
        members = it.get("members") or []
        if members:
            parts.append(
                '<details><summary>本次采集命中的成分股（'
                + esc(len(members))
                + "）</summary>"
                + f'<p class="sub">{esc("、".join(str(m) for m in members))}</p></details>'
            )
        parts.append(_note(it.get("note")))
        parts.append("</div>")
    return "".join(parts)


def _news_list(items: Sequence[Mapping[str, Any]], limit: int = 12) -> str:
    if not items:
        return f'<p class="note">{MISSING} 无可用条目</p>'
    lis = []
    for item in items[:limit]:
        meta = " ".join(str(x) for x in (item.get("time"), item.get("media") or item.get("source")) if x)
        lis.append(f'<li>{_link(item.get("title"), item.get("url"))} <span class="sub">{esc(meta)}</span></li>')
    return f'<ul class="news">{"".join(lis)}</ul>'


def _section_sentiment(payload: Mapping[str, Any]) -> str:
    data = payload.get("sentiment") or {}
    em = data.get("eastmoney") or {}
    ths = data.get("ths") or {}
    parts = ['<h2 id="sentiment">5. 热点与评论（东方财富 / 同花顺）</h2>']

    comment = em.get("comment") or []
    comment_rows = [
        [
            f"{esc(row.get('name'))} <span class=\"sub\">{esc(row.get('code'))}</span>",
            fmt_num(row.get("score"), 2),
            esc(row.get("rank")),
            fmt_num(row.get("org_participation"), 2, "%"),
            fmt_num(row.get("focus"), 2),
            esc(row.get("comment")),
        ]
        for row in comment[:30]
    ]
    parts.append(
        '<div class="card"><h3>东方财富千股千评</h3>'
        + _table(
            ["股票", "综合得分", "排名", "机构参与度", "关注指数", "评论/诊断"],
            comment_rows,
            text_columns=(0, 5),
        )
        + _note(em.get("comment_note"))
        + "</div>"
    )

    active = em.get("hot") or []
    if active:
        rows = [
            [
                f"{esc(row.get('name'))} <span class=\"sub\">{esc(row.get('code'))}</span>",
                fmt_pct(row.get("pct_chg")),
                fmt_money(row.get("amount")),
                fmt_num(row.get("turnover"), 2, "%"),
                esc(row.get("rank")),
            ]
            for row in active[:20]
        ]
        parts.append(
            '<div class="card"><h3>东方财富热度/活跃度榜</h3>'
            + _table(["股票", "涨跌幅", "成交额", "换手率", "榜单名次"], rows)
            + _note(em.get("hot_note"))
            + "</div>"
        )

    news_map = em.get("news") or {}
    if news_map:
        blocks = []
        for code, items in news_map.items():
            blocks.append(
                f"<details><summary>{esc(code)}（{esc(len(items))} 条）</summary>{_news_list(items)}</details>"
            )
        parts.append('<div class="card"><h3>东方财富个股资讯</h3>' + "".join(blocks) + "</div>")

    parts.append(
        '<div class="card"><h3>同花顺热点快讯</h3>'
        + _news_list(ths.get("news") or [], 20)
        + _note(ths.get("news_note"))
        + "</div>"
    )

    boards = ths.get("boards") or []
    if boards:
        rows = [
            [esc(row.get("board")), esc(row.get("pct_chg")), esc(row.get("rank")), esc(row.get("detail"))]
            for row in boards[:20]
        ]
        parts.append(
            '<div class="card"><h3>同花顺热门板块/概念</h3>'
            + _table(["板块", "涨跌幅", "排名", "备注"], rows, text_columns=(0, 3))
            + _note(ths.get("boards_note"))
            + "</div>"
        )
    parts.append(_note(data.get("notes")))
    return "".join(parts)


def _section_sources(payload: Mapping[str, Any]) -> str:
    events = payload.get("events") or []
    ak = (payload.get("meta") or {}).get("akshare") or {}
    parts = ['<h2 id="sources">6. 数据源与缺失说明</h2>', '<div class="card">']
    parts.append(
        '<div class="grid">'
        + _kv("akshare 可用", "是" if ak.get("available") else "否")
        + _kv("akshare 版本", esc(ak.get("version")))
        + _kv("采集事件数", esc(len(events)))
        + _kv("兜底次数", esc(len([e for e in events if e.get("status") == "fallback"])))
        + _kv("缺失/失败", esc(len([e for e in events if e.get("status") in ("missing", "error")])))
        + "</div>"
    )
    if ak.get("import_error"):
        parts.append(_note(f"akshare 不可用原因：{ak['import_error']}"))
    rows = [
        [
            esc(e.get("step")),
            esc(e.get("status")),
            esc(e.get("source")),
            esc(e.get("rows")),
            esc(e.get("detail")),
        ]
        for e in events
        if e.get("status") != "ok"
    ]
    parts.append("<h4>非正常事件（兜底 / 缺失 / 失败）</h4>")
    parts.append(_table(["步骤", "状态", "实际数据源", "行数", "详情"], rows, text_columns=(0, 2, 4)))
    parts.append("</div>")
    return "".join(parts)


def render_report(payload: Mapping[str, Any]) -> str:
    """渲染完整 HTML 报告。``payload`` 结构见 ``pipeline.build_payload``。"""
    meta = payload.get("meta") or {}
    config_summary = meta.get("config") or {}
    generated_at = meta.get("generated_at") or datetime.now().astimezone().isoformat(timespec="seconds")
    title = meta.get("title") or "A 股近一年详细信息报告"

    kvs = "".join(_kv(str(k), esc(v if not isinstance(v, list) else "、".join(str(i) for i in v) or MISSING)) for k, v in config_summary.items())
    head = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN"><head><meta charset="utf-8"/>',
        '<meta name="viewport" content="width=device-width,initial-scale=1"/>',
        f"<title>{esc(title)}</title>",
        f"<style>{CSS}</style></head><body><div class=\"wrap\">",
        f"<h1>{esc(title)}</h1>",
        f'<p class="sub">生成时间：{esc(generated_at)}｜数据源：akshare 优先，不可用时兜底东方财富 / 同花顺 公开接口。'
        "本报告仅作数据汇总，不构成投资建议。</p>",
        f'<div class="grid">{kvs}</div>',
        '<p class="sub">目录：<a href="#index">指数</a> ｜ <a href="#new">新股总览</a> ｜ '
        '<a href="#profile">个股画像</a> ｜ <a href="#sector">板块</a> ｜ '
        '<a href="#sentiment">热点评论</a> ｜ <a href="#sources">数据源说明</a>'
        '<br/>本程序只采集新股：股票池为近一年内上市的全部新股；每只新股的明细单独成页（stocks/&lt;代码&gt;.html）。</p>',
    ]
    body = [
        _section_indexes(payload),
        _section_new_stocks(payload),
        _section_profiles(payload),
        _section_sectors(payload),
        _section_sentiment(payload),
        _section_sources(payload),
    ]
    footer = (
        "<footer>诚实性说明：所有数字均来自上述公开接口实时抓取，不可得的字段以 — 标记并在第 6 节列出原因；"
        "分时数据受接口保留期限制，历史日期可能不可回溯。第三方接口字段定义可能变动，使用前请交叉校验。</footer>"
        "</div></body></html>"
    )
    return "".join(head + body + [footer])


def _stock_detail_body(stock: Mapping[str, Any], profile: Mapping[str, Any]) -> list[str]:
    """单只新股明细页的正文块。"""
    since = stock.get("since_ipo") or {}
    parts: list[str] = ['<div class="card">']
    parts.append(
        '<div class="grid">'
        + _kv("上市日期", esc(stock.get("list_date") or profile.get("list_date")))
        + _kv("最新价", fmt_num(profile.get("price")))
        + _kv("涨跌幅", fmt_pct(profile.get("pct_chg")))
        + _kv("上市以来涨跌", fmt_pct(since.get("pct_chg")))
        + _kv("首日收盘", fmt_num(since.get("first_close")))
        + _kv("最新收盘", fmt_num(since.get("last_close")))
        + _kv("总市值", fmt_money(profile.get("total_mv")))
        + _kv("流通市值", fmt_money(profile.get("float_mv")))
        + _kv("市盈率(静)", fmt_num(profile.get("pe_static")))
        + _kv("市盈率(动)", fmt_num(profile.get("pe_dynamic")))
        + _kv("市盈率(TTM)", fmt_num(profile.get("pe_ttm")))
        + _kv("市净率", fmt_num(profile.get("pb")))
        + _kv("换手率", fmt_num(profile.get("turnover"), 2, "%"))
        + _kv("所属板块", esc(profile.get("board")))
        + _kv("所属行业", esc(profile.get("industry")))
        + _kv("交易日数", esc(since.get("rows")))
        + "</div>"
        + _source_tag(stock.get("source") or profile.get("source"))
    )
    parts.append("</div>")

    parts.append('<div class="card"><h3>上市至今行情</h3>')
    if since.get("line_svg") or since.get("candle_svg"):
        parts.append(since.get("line_svg") or "")
        if since.get("candle_svg"):
            parts.append("<h4>上市至今日 K 线</h4>" + since["candle_svg"])
    else:
        parts.append(f'<p class="note">{MISSING} 未取到上市至今日线数据</p>')
    parts.append(_note(stock.get("notes") or stock.get("note")))
    parts.append("</div>")

    first_days = stock.get("first_days") or []
    parts.append('<div class="card"><h3>上市初期逐日行情与分时</h3>')
    if first_days:
        rows = [
            [
                esc(day.get("date")),
                fmt_num(day.get("open")),
                fmt_num(day.get("high")),
                fmt_num(day.get("low")),
                fmt_num(day.get("close")),
                fmt_pct(day.get("pct_chg")),
                fmt_num(day.get("turnover"), 2, "%"),
                fmt_money(day.get("amount")),
            ]
            for day in first_days
        ]
        parts.append(
            f"<h4>上市前 {len(first_days)} 个交易日</h4>"
            + _table(["日期", "开盘", "最高", "最低", "收盘", "涨跌幅", "换手率", "成交额"], rows)
        )
        for day in first_days:
            intraday = day.get("intraday") or {}
            label = f"{esc(day.get('date'))} 分时图"
            if intraday.get("granularity"):
                label += f'<span class="tag">{esc(intraday["granularity"])}</span>'
            parts.append(f"<h4>{label}</h4>")
            parts.append(intraday.get("svg") or "")
            parts.append(_note(intraday.get("note")))
    else:
        parts.append(f'<p class="note">{MISSING} 未取到上市初期日线数据</p>')
    parts.append("</div>")

    holders = profile.get("holders") or {}
    holder_rows = [
        [
            esc(h.get("holder")),
            esc(h.get("rank")),
            fmt_shares(h.get("shares")),
            fmt_num(h.get("ratio"), 2, "%"),
            esc(h.get("change")),
            esc(h.get("holder_type")),
        ]
        for h in (holders.get("rows") or [])
    ]
    parts.append(
        '<div class="card">'
        + f"<h3>前十大流通股东（报告期 {esc(holders.get('report_date'))}，来源 {esc(holders.get('source'))}）</h3>"
        + _table(
            ["股东名称", "名次", "持股数量", "占流通股比例", "增减情况", "股东性质"],
            holder_rows,
            text_columns=(0, 4, 5),
        )
        + _note(holders.get("note"))
        + "</div>"
    )

    business = profile.get("main_business")
    parts.append('<div class="card"><h3>主营业务</h3>')
    if business:
        parts.append(
            f'<p class="sub" style="font-size:12.5px;color:var(--ink)">{esc(business)}</p>'
            + _source_tag(profile.get("main_business_source"))
        )
    else:
        parts.append(f'<p class="note">{MISSING} 未取到主营业务描述</p>')
    if profile.get("valuation_svg"):
        parts.append("<h4>估值（市盈率）走势</h4>" + profile["valuation_svg"])
    parts.append(_note(profile.get("notes") or profile.get("note")))
    parts.append("</div>")
    return parts


def render_stock_page(
    payload: Mapping[str, Any],
    stock: Mapping[str, Any] | None = None,
    profile: Mapping[str, Any] | None = None,
) -> str:
    """渲染单只新股的明细页（总览页只留汇总表，避免单页体积失控）。"""
    stock = stock or {}
    profile = profile or {}
    code = stock.get("code") or profile.get("code")
    name = stock.get("name") or profile.get("name")
    meta = payload.get("meta") or {}
    generated_at = meta.get("generated_at") or datetime.now().astimezone().isoformat(timespec="seconds")
    title = f"{esc(name)}（{esc(code)}）新股明细"
    head = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN"><head><meta charset="utf-8"/>',
        '<meta name="viewport" content="width=device-width,initial-scale=1"/>',
        f"<title>{title}</title>",
        f'<style>{CSS}</style></head><body><div class="wrap">',
        f"<h1>{title}</h1>",
        f'<p class="sub">生成时间：{esc(generated_at)}｜<a href="../index.html">返回总览</a>｜'
        "数据源：akshare 优先，不可用时兜底东方财富 / 同花顺公开接口；不可得字段一律标 —，不估算、不编造。</p>",
    ]
    footer = (
        "<footer>本页由 ashare_yearly 自动生成，仅作公开数据汇总，不构成投资建议。"
        "缺失原因汇总见总览页“数据源与缺失说明”。</footer></div></body></html>"
    )
    return "".join(head + _stock_detail_body(stock, profile) + [footer])
