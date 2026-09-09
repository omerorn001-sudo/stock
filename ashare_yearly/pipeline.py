"""报告组装与执行入口：调用 collect.py 的采集能力，落盘 payload 并渲染 HTML 报告。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

from . import charts
from . import codes as codeutil
from .config import Config
from .frames import (
    clip_range,
    first_n_rows,
    period_return,
    pick_column,
    records,
    row_value,
    safe_float,
    series_points,
    ymd,
)
from .netutil import Http
from .report import render_report
from .sources import AkAdapter
from .sources import eastmoney as em
from .sources import ths
from .store import Store
from .collect import (
    Context,
    NUMERIC_SPOT,
    Provider,
    SPOT_ALIASES,
    _em_minute_day,
    _em_trends_day,
    _empty,
    _holders,
    _individual_info,
    _intraday,
    _ipo_frame,
    _main_business,
    _normalize_spot,
    _size,
    collect_indexes,
    collect_new_stocks,
    load_spot,
    report_period_candidates,
    resolve_universe,
    spot_row,
    try_chain,
)


def _valuation_svg(ctx: Context, code: str, name: str | None) -> tuple[str | None, str | None]:
    cfg = ctx.config
    df, source = try_chain(
        ctx,
        f"valuation:{code}",
        [("akshare:stock_a_indicator_lg", lambda: ctx.ak.valuation_hist(code))],
    )
    if df is None:
        return None, None
    frame = pd.DataFrame(df)
    date_col = pick_column(frame, ["trade_date", "date", "\u65e5\u671f"])
    value_col = pick_column(frame, ["pe_ttm", "pe", "\u5e02\u76c8\u7387"])
    if not date_col or not value_col:
        return None, source
    tidy = pd.DataFrame(
        {"date": frame[date_col].map(ymd), "close": frame[value_col].map(safe_float)}
    ).dropna(subset=["close"])
    tidy = clip_range(tidy, cfg.start_dash, cfg.end_dash)
    if not len(tidy):
        return None, source
    ctx.store.write_csv(tidy, f"profile/{code}/valuation.csv")
    return (
        charts.line_chart(
            series_points(tidy),
            title=f"{name or code} \u5e02\u76c8\u7387\u8fd1\u4e00\u5e74",
            color="#2f6fd0",
            area=False,
        ),
        source,
    )


def collect_profiles(ctx: Context, universe: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for entry in universe:
        code = entry.get("code")
        if not code:
            continue
        notes: list[str] = []
        row = spot_row(ctx, code)
        info = _individual_info(ctx, code)
        name = entry.get("name") or row.get("name") or row_value(info, ["\u80a1\u7968\u7b80\u79f0", "\u540d\u79f0"])

        total_mv = safe_float(row.get("total_mv")) or safe_float(row_value(info, ["\u603b\u5e02\u503c"]))
        float_mv = safe_float(row.get("float_mv")) or safe_float(row_value(info, ["\u6d41\u901a\u5e02\u503c"]))
        industry = row.get("industry") or row_value(info, ["\u884c\u4e1a"])
        list_date = entry.get("list_date") or row.get("list_date") or ymd(row_value(info, ["\u4e0a\u5e02\u65f6\u95f4"]) or "")
        if not row:
            notes.append("\u5168\u5e02\u573a\u5feb\u7167\u672a\u547d\u4e2d\u8be5\u4ee3\u7801\uff0c\u884c\u60c5\u7c7b\u5b57\u6bb5\u53ef\u80fd\u7f3a\u5931")

        holders = _holders(ctx, code)
        business, business_source = _main_business(ctx, code, name)
        valuation_svg, valuation_source = _valuation_svg(ctx, code, name)
        if valuation_svg is None:
            notes.append("\u4f30\u503c\u5386\u53f2\uff08PE \u5e8f\u5217\uff09\u4e0d\u53ef\u5f97\uff0c\u4ec5\u5c55\u793a\u5feb\u7167\u5e02\u76c8\u7387")

        items.append(
            {
                "code": code,
                "name": name,
                "board": codeutil.board(code),
                "industry": industry,
                "list_date": list_date or None,
                "price": safe_float(row.get("price")),
                "pct_chg": safe_float(row.get("pct_chg")),
                "total_mv": total_mv,
                "float_mv": float_mv,
                "pe_static": safe_float(row.get("pe_static")),
                "pe_dynamic": safe_float(row.get("pe_dynamic")),
                "pe_ttm": safe_float(row.get("pe_ttm")),
                "pb": safe_float(row.get("pb")),
                "turnover": safe_float(row.get("turnover")),
                "main_business": business,
                "main_business_source": business_source,
                "holders": holders,
                "valuation_svg": valuation_svg,
                "source": ctx.spot_source or valuation_source,
                "notes": notes,
            }
        )
    if items:
        ctx.store.write_json([{k: v for k, v in it.items() if not str(k).endswith("_svg")} for it in items], "profile/profiles.json")
    return items


# ---------------- 4. \u677f\u5757 ----------------
def _board_map(ctx: Context) -> dict[str, str]:
    if ctx.board_map:
        return ctx.board_map
    df, _ = try_chain(
        ctx,
        "industry_boards",
        [
            ("akshare:stock_board_industry_name_em", lambda: ctx.ak.industry_boards()),
            ("eastmoney:clist(boards)", lambda: em.industry_boards(ctx.http)),
        ],
    )
    if df is None:
        return {}
    frame = pd.DataFrame(df)
    name_col = pick_column(frame, ["board_name", "\u677f\u5757\u540d\u79f0", "name", "\u540d\u79f0"])
    code_col = pick_column(frame, ["board_code", "\u677f\u5757\u4ee3\u7801", "code", "\u4ee3\u7801"])
    if not name_col:
        return {}
    mapping = {}
    for raw in records(frame):
        key = str(raw.get(name_col) or "").strip()
        if key:
            mapping[key] = str(raw.get(code_col) or "").strip() if code_col else ""
    ctx.board_map = mapping
    ctx.store.write_csv(frame, "sector/boards.csv")
    return mapping


def collect_sectors(
    ctx: Context, profiles: Sequence[Mapping[str, Any]], limit: int = 8
) -> tuple[list[dict[str, Any]], str | None]:
    cfg = ctx.config
    mapping = _board_map(ctx)
    grouped: dict[str, list[str]] = {}
    for profile in profiles:
        industry = str(profile.get("industry") or "").strip()
        if not industry:
            continue
        grouped.setdefault(industry, []).append(str(profile.get("code")))
    if not grouped:
        ctx.store.record("sector", status="missing", detail="\u4e2a\u80a1\u753b\u50cf\u91cc\u6ca1\u6709\u53ef\u7528\u7684\u884c\u4e1a\u5b57\u6bb5")
        return [], None

    ordered = sorted(grouped.items(), key=lambda kv: len(kv[1]), reverse=True)[:limit]
    items: list[dict[str, Any]] = []
    series: dict[str, list[tuple[str, float]]] = {}
    for board_name, members in ordered:
        board_code = mapping.get(board_name) or ""
        providers: list[Provider] = [
            (
                "akshare:stock_board_industry_hist_em",
                lambda board_name=board_name: ctx.ak.industry_board_hist(board_name, cfg.start_ymd, cfg.end_ymd),
            )
        ]
        if board_code:
            providers.append(
                (
                    f"eastmoney:kline(90.{board_code})",
                    lambda board_code=board_code: em.board_kline(ctx.http, board_code, cfg.start_ymd, cfg.end_ymd),
                )
            )
        df, source = try_chain(ctx, f"sector:{board_name}", providers)
        if df is None:
            items.append(
                {
                    "name": board_name,
                    "code": board_code or None,
                    "source": None,
                    "members": members,
                    "line_svg": charts.empty_chart(f"{board_name} \u677f\u5757\u884c\u60c5\u4e0d\u53ef\u5f97"),
                    "note": "akshare \u4e0e\u4e1c\u8d22\u677f\u5757 K \u7ebf\u5747\u672a\u8fd4\u56de\u6570\u636e\uff08\u884c\u4e1a\u540d\u79f0\u53ef\u80fd\u4e0e\u4e1c\u8d22\u677f\u5757\u540d\u4e0d\u4e00\u81f4\uff09",
                }
            )
            continue
        df = clip_range(df, cfg.start_dash, cfg.end_dash)
        ctx.store.write_csv(df, f"sector/{board_name}.csv")
        points = series_points(df)
        items.append(
            {
                "name": board_name,
                "code": board_code or None,
                "source": source,
                "rows": len(df),
                "pct_chg": period_return(df),
                "last_close": points[-1][1] if points else None,
                "members": members,
                "line_svg": charts.line_chart(points, title=f"{board_name} \u8fd1\u4e00\u5e74\u8d70\u52bf"),
                "note": None,
            }
        )
        if points:
            series[board_name] = points
    compare = charts.multi_line_chart(series, title="\u677f\u5757\u5bf9\u6bd4") if series else None
    return items, compare


# ---------------- 5. \u70ed\u70b9\u4e0e\u8bc4\u8bba ----------------
def _normalize_comment(frame: pd.DataFrame, codes: set[str]) -> list[dict[str, Any]]:
    code_col = pick_column(frame, ["code", "\u4ee3\u7801", "SECURITY_CODE"])
    name_col = pick_column(frame, ["name", "\u540d\u79f0", "SECURITY_NAME_ABBR"])
    score_col = pick_column(frame, ["\u7efc\u5408\u5f97\u5206", "TOTAL_SCORE", "score"])
    rank_col = pick_column(frame, ["\u76ee\u524d\u6392\u540d", "RANK", "\u6392\u540d"])
    org_col = pick_column(frame, ["\u673a\u6784\u53c2\u4e0e\u5ea6", "ORG_PARTICIPATE"])
    focus_col = pick_column(frame, ["\u5173\u6ce8\u6307\u6570", "FOCUS"])
    text_col = pick_column(frame, ["\u8bca\u65ad", "\u8bc4\u8bba", "COMMENT", "\u4e3b\u529b\u6210\u672c"])
    if not code_col:
        return []
    rows: list[dict[str, Any]] = []
    for raw in records(frame):
        code = str(raw.get(code_col) or "").zfill(6)[-6:]
        if codes and code not in codes:
            continue
        org = safe_float(raw.get(org_col)) if org_col else None
        if org is not None and org <= 1:
            org *= 100
        rows.append(
            {
                "code": code,
                "name": raw.get(name_col) if name_col else None,
                "score": safe_float(raw.get(score_col)) if score_col else None,
                "rank": raw.get(rank_col) if rank_col else None,
                "org_participation": org,
                "focus": safe_float(raw.get(focus_col)) if focus_col else None,
                "comment": raw.get(text_col) if text_col else None,
            }
        )
    rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    return rows


def _normalize_news(frame: pd.DataFrame, limit: int) -> list[dict[str, Any]]:
    title_col = pick_column(frame, ["title", "\u65b0\u95fb\u6807\u9898", "\u6807\u9898"])
    url_col = pick_column(frame, ["url", "\u65b0\u95fb\u94fe\u63a5", "\u94fe\u63a5"])
    time_col = pick_column(frame, ["time", "\u53d1\u5e03\u65f6\u95f4", "\u65f6\u95f4", "\u65e5\u671f"])
    media_col = pick_column(frame, ["media", "\u6587\u7ae0\u6765\u6e90", "\u6765\u6e90", "source"])
    if not title_col:
        return []
    rows = []
    for raw in records(frame)[:limit]:
        rows.append(
            {
                "title": str(raw.get(title_col) or "").strip(),
                "url": raw.get(url_col) if url_col else None,
                "time": str(raw.get(time_col) or "").strip() if time_col else None,
                "media": raw.get(media_col) if media_col else None,
            }
        )
    return [r for r in rows if r["title"]]


def collect_sentiment(ctx: Context, universe: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    cfg = ctx.config
    codes = {str(u.get("code")) for u in universe if u.get("code")}
    result: dict[str, Any] = {"eastmoney": {}, "ths": {}, "notes": []}

    comment_df, comment_source = try_chain(
        ctx,
        "em_comment",
        [
            ("akshare:stock_comment_em", lambda: ctx.ak.stock_comment()),
            ("eastmoney:RPT_DMSK_TS_STOCKNEW", lambda: em.stock_comment(ctx.http, pages=3)),
        ],
    )
    if comment_df is not None:
        frame = pd.DataFrame(comment_df)
        ctx.store.write_csv(frame, "sentiment/em_comment_raw.csv")
        rows = _normalize_comment(frame, codes)
        if not rows:
            rows = _normalize_comment(frame, set())[:30]
            result["eastmoney"]["comment_note"] = "\u672c\u6b21\u80a1\u7968\u6c60\u5728\u5343\u80a1\u5343\u8bc4\u91cc\u672a\u547d\u4e2d\uff08\u65b0\u80a1\u5e38\u89c1\uff09\uff0c\u6539\u4e3a\u5c55\u793a\u5f97\u5206\u524d\u5217\u4e2a\u80a1"
        result["eastmoney"]["comment"] = rows
        result["eastmoney"]["comment_source"] = comment_source
    else:
        result["eastmoney"]["comment"] = []
        result["eastmoney"]["comment_note"] = "\u5343\u80a1\u5343\u8bc4\u63a5\u53e3\u4e0d\u53ef\u5f97"

    hot_df, hot_source = try_chain(
        ctx,
        "em_hot",
        [
            ("akshare:stock_hot_rank_em", lambda: ctx.ak.hot_rank()),
            ("eastmoney:clist(\u6210\u4ea4\u989d\u699c)", lambda: em.most_active(ctx.http, top=30)),
        ],
    )
    if hot_df is not None:
        frame = _normalize_spot(pd.DataFrame(hot_df))
        if not len(frame):
            frame = pd.DataFrame(hot_df)
        ctx.store.write_csv(pd.DataFrame(hot_df), "sentiment/em_hot_raw.csv")
        rank_col = pick_column(pd.DataFrame(hot_df), ["\u5f53\u524d\u6392\u540d", "\u6392\u540d", "rank"])
        raw_rows = records(pd.DataFrame(hot_df))
        rows = []
        for index, normalized in enumerate(records(frame)):
            raw = raw_rows[index] if index < len(raw_rows) else {}
            rows.append(
                {
                    "code": normalized.get("code"),
                    "name": normalized.get("name"),
                    "pct_chg": safe_float(normalized.get("pct_chg")),
                    "amount": safe_float(normalized.get("amount")),
                    "turnover": safe_float(normalized.get("turnover")),
                    "rank": raw.get(rank_col) if rank_col else index + 1,
                }
            )
        result["eastmoney"]["hot"] = rows
        result["eastmoney"]["hot_note"] = (
            "\u4e1c\u8d22\u4eba\u6c14\u699c\u9700 POST \u63a5\u53e3\uff0cakshare \u4e0d\u53ef\u7528\u65f6\u4ee5\u6210\u4ea4\u989d\u6d3b\u8dc3\u5ea6\u699c\u4f5c\u516c\u5f00\u66ff\u4ee3"
            if hot_source and hot_source.startswith("eastmoney")
            else None
        )

    news_map: dict[str, list[dict[str, Any]]] = {}
    for entry in list(universe)[: min(10, len(universe))]:
        code = str(entry.get("code") or "")
        if not code:
            continue
        keyword = str(entry.get("name") or code)
        df, _ = try_chain(
            ctx,
            f"em_news:{code}",
            [
                ("akshare:stock_news_em", lambda code=code: ctx.ak.stock_news(code)),
                ("eastmoney:search", lambda keyword=keyword: em.stock_news(ctx.http, keyword, size=cfg.news_per_stock)),
            ],
        )
        if df is None:
            continue
        rows = _normalize_news(pd.DataFrame(df), cfg.news_per_stock)
        if rows:
            label = f"{keyword} {code}" if keyword != code else code
            news_map[label] = rows
    result["eastmoney"]["news"] = news_map

    ths_df, ths_source = try_chain(
        ctx,
        "ths_news",
        [
            ("akshare:stock_info_global_ths", lambda: ctx.ak.ths_news()),
            ("ths:news.10jqka", lambda: ths.hot_news(ctx.http, pages=2)),
        ],
    )
    if ths_df is not None:
        frame = pd.DataFrame(ths_df)
        ctx.store.write_csv(frame, "sentiment/ths_news.csv")
        result["ths"]["news"] = _normalize_news(frame, 25)
        result["ths"]["news_source"] = ths_source
    else:
        result["ths"]["news"] = []
        result["ths"]["news_note"] = "\u540c\u82b1\u987a\u5feb\u8baf\u63a5\u53e3\u4e0d\u53ef\u5f97\uff08\u90e8\u5206\u9875\u9762\u9700 hexin-v Cookie\uff09"

    boards_df, boards_source = try_chain(
        ctx,
        "ths_boards",
        [
            ("akshare:stock_hot_rank_wc", lambda: ctx.ak.ths_hot_rank()),
            ("ths:q.10jqka\u6982\u5ff5\u699c", lambda: ths.concept_rank(ctx.http, pages=1)),
        ],
    )
    if boards_df is not None:
        frame = pd.DataFrame(boards_df)
        ctx.store.write_csv(frame, "sentiment/ths_boards.csv")
        board_col = pick_column(frame, ["board", "\u677f\u5757", "\u6982\u5ff5\u540d\u79f0", "\u80a1\u7968\u540d\u79f0", "name", "\u540d\u79f0"])
        pct_col = pick_column(frame, ["pct_chg", "\u6da8\u8dcc\u5e45", "\u6da8\u5e45"])
        rank_col = pick_column(frame, ["rank", "\u6392\u540d", "\u5e8f\u53f7"])
        detail_col = pick_column(frame, ["detail", "\u5907\u6ce8", "\u9886\u6da8\u80a1", "\u6d41\u5165\u8d44\u91d1"])
        rows = []
        for raw in records(frame)[:25]:
            rows.append(
                {
                    "board": raw.get(board_col) if board_col else None,
                    "pct_chg": raw.get(pct_col) if pct_col else None,
                    "rank": raw.get(rank_col) if rank_col else None,
                    "detail": raw.get(detail_col) if detail_col else None,
                }
            )
        result["ths"]["boards"] = rows
        result["ths"]["boards_source"] = boards_source
    else:
        result["ths"]["boards"] = []
        result["ths"]["boards_note"] = "\u540c\u82b1\u987a\u70ed\u95e8\u677f\u5757\u699c\u4e0d\u53ef\u5f97"

    result["notes"] = [
        "\u8d44\u8baf\u4e0e\u8bc4\u8bba\u4e3a\u7b2c\u4e09\u65b9\u5e73\u53f0\u5185\u5bb9\u539f\u6587\u94fe\u63a5\uff0c\u672a\u505a\u60c5\u611f\u52a0\u5de5\uff0c\u4e0d\u4ee3\u8868\u672c\u9879\u76ee\u89c2\u70b9",
    ]
    return result


# ---------------- \u62a5\u544a\u7ec4\u88c5 ----------------
def _strip_svg(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_svg(v) for k, v in value.items() if not str(k).endswith("_svg") and k != "svg"}
    if isinstance(value, list):
        return [_strip_svg(v) for v in value]
    return value


def build_payload(ctx: Context, sections: Mapping[str, Any]) -> dict[str, Any]:
    cfg = ctx.config
    return {
        "meta": {
            "title": f"A \u80a1\u8fd1\u4e00\u5e74\u8be6\u7ec6\u4fe1\u606f\u62a5\u544a\uff08{cfg.start_dash} ~ {cfg.end_dash}\uff09",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "config": cfg.summary(),
            "akshare": ctx.ak.info(),
        },
        "indexes": sections.get("indexes") or [],
        "index_compare_svg": sections.get("index_compare_svg"),
        "new_stocks": sections.get("new_stocks") or [],
        "profiles": sections.get("profiles") or [],
        "sectors": sections.get("sectors") or [],
        "sector_compare_svg": sections.get("sector_compare_svg"),
        "sentiment": sections.get("sentiment") or {},
        "events": ctx.store.events,
    }


def run(config: Config) -> dict[str, Any]:
    """\u6267\u884c\u4e00\u6b21\u5b8c\u6574\u91c7\u96c6\u5e76\u751f\u6210\u62a5\u544a\u3002"""
    ak = AkAdapter(enabled=config.use_akshare)
    http = Http(
        min_interval=config.min_interval,
        timeout=config.timeout,
        retries=config.retries,
        cache_dir=config.cache_dir if config.use_cache else None,
    )
    store = Store(out_dir=config.out_dir, data_dir=config.data_dir or config.out_dir / "data")
    ctx = Context(config=config, ak=ak, http=http, store=store)
    if not ak.available:
        store.record("akshare", status="fallback", detail=f"akshare \u4e0d\u53ef\u7528\uff0c\u5df2\u5207\u6362\u5230\u516c\u5f00\u63a5\u53e3\u515c\u5e95\uff1a{ak.import_error}")

    sections: dict[str, Any] = {}
    universe: list[dict[str, Any]] = []

    if config.enabled("index"):
        indexes, compare = collect_indexes(ctx)
        sections["indexes"] = indexes
        sections["index_compare_svg"] = compare

    needs_universe = any(config.enabled(step) for step in ("new", "profile", "sector", "sentiment"))
    if needs_universe:
        universe = resolve_universe(ctx)
        store.write_json(universe, "universe.json")

    if config.enabled("new"):
        sections["new_stocks"] = collect_new_stocks(ctx, universe)
    if config.enabled("profile"):
        sections["profiles"] = collect_profiles(ctx, universe)
    if config.enabled("sector"):
        sectors, sector_compare = collect_sectors(ctx, sections.get("profiles") or [])
        sections["sectors"] = sectors
        sections["sector_compare_svg"] = sector_compare
    if config.enabled("sentiment"):
        sections["sentiment"] = collect_sentiment(ctx, universe)

    payload = build_payload(ctx, sections)
    result: dict[str, Any] = {"payload": payload}

    if config.enabled("report"):
        html = render_report(payload)
        result["report"] = store.write_text(html, "index.html")
        result["dated_report"] = store.write_text(html, f"ashare-yearly-{config.end_dash}.html")
        result["payload_json"] = store.write_json(_strip_svg(payload), "payload.json")

    result["manifest"] = store.save_manifest(config.summary(), ak.info())
    result["events"] = store.events
    result["universe"] = universe
    return result


def self_check(config: Config) -> dict[str, Any]:
    """\u8f7b\u91cf\u81ea\u68c0\uff1a\u9010\u4e2a\u63a2\u6d4b\u5173\u952e\u80fd\u529b\uff0c\u4e0d\u5199\u62a5\u544a\uff0c\u4fbf\u4e8e\u9996\u6b21\u90e8\u7f72\u9a8c\u8bc1\u3002"""
    ak = AkAdapter(enabled=config.use_akshare)
    http = Http(
        min_interval=config.min_interval,
        timeout=config.timeout,
        retries=1,
        cache_dir=config.cache_dir if config.use_cache else None,
    )
    store = Store(out_dir=config.out_dir, data_dir=config.data_dir or config.out_dir / "data")
    ctx = Context(config=config, ak=ak, http=http, store=store)
    spec = config.indexes[0]
    probes: list[tuple[str, list[Provider]]] = [
        (
            "\u6307\u6570\u65e5\u7ebf",
            [
                ("akshare:index_hist", lambda: ak.index_hist(spec.code, spec.ak_symbol, config.start_ymd, config.end_ymd)),
                ("eastmoney:kline", lambda: em.kline(http, spec.secid, config.start_ymd, config.end_ymd, fqt=0)),
            ],
        ),
        (
            "\u5168\u5e02\u573a\u5feb\u7167",
            [
                ("akshare:stock_zh_a_spot_em", lambda: _normalize_spot(ak.spot_all())),
                ("eastmoney:clist", lambda: _normalize_spot(em.spot_all(http))),
            ],
        ),
        (
            "\u65b0\u80a1\u540d\u5355",
            [("akshare:stock_xgsglb_em", lambda: ak.ipo_list())],
        ),
        (
            "\u540c\u82b1\u987a\u5feb\u8baf",
            [
                ("akshare:stock_info_global_ths", lambda: ak.ths_news()),
                ("ths:news.10jqka", lambda: ths.hot_news(http, pages=1)),
            ],
        ),
    ]
    outcome: list[dict[str, Any]] = []
    for label, providers in probes:
        result, source = try_chain(ctx, f"self_check:{label}", providers)
        outcome.append(
            {
                "capability": label,
                "ok": result is not None,
                "source": source,
                "rows": _size(result) if result is not None else 0,
            }
        )
    return {
        "akshare": ak.info(),
        "probes": outcome,
        "events": store.events,
    }
