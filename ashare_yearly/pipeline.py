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
from .report import render_report, render_stock_page
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
    date_col = pick_column(frame, ["trade_date", "date", "日期"])
    value_col = pick_column(frame, ["pe_ttm", "pe", "市盈率"])
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
            title=f"{name or code} 市盈率近一年",
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
        name = entry.get("name") or row.get("name") or row_value(info, ["股票简称", "名称"])

        total_mv = safe_float(row.get("total_mv")) or safe_float(row_value(info, ["总市值"]))
        float_mv = safe_float(row.get("float_mv")) or safe_float(row_value(info, ["流通市值"]))
        industry = row.get("industry") or row_value(info, ["行业"])
        list_date = entry.get("list_date") or row.get("list_date") or ymd(row_value(info, ["上市时间"]) or "")
        if not row:
            notes.append("全市场快照未命中该代码，行情类字段可能缺失")

        holders = _holders(ctx, code)
        business, business_source = _main_business(ctx, code, name)
        valuation_svg, valuation_source = _valuation_svg(ctx, code, name)
        if valuation_svg is None:
            notes.append("估值历史（PE 序列）不可得，仅展示快照市盈率")

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


# ---------------- 4. 板块 ----------------
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
    name_col = pick_column(frame, ["board_name", "板块名称", "name", "名称"])
    code_col = pick_column(frame, ["board_code", "板块代码", "code", "代码"])
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
        ctx.store.record("sector", status="missing", detail="个股画像里没有可用的行业字段")
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
                    "line_svg": charts.empty_chart(f"{board_name} 板块行情不可得"),
                    "note": "akshare 与东财板块 K 线均未返回数据（行业名称可能与东财板块名不一致）",
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
                "line_svg": charts.line_chart(points, title=f"{board_name} 近一年走势"),
                "note": None,
            }
        )
        if points:
            series[board_name] = points
    compare = charts.multi_line_chart(series, title="板块对比") if series else None
    return items, compare


# ---------------- 5. 热点与评论 ----------------
def _normalize_comment(frame: pd.DataFrame, codes: set[str]) -> list[dict[str, Any]]:
    code_col = pick_column(frame, ["code", "代码", "SECURITY_CODE"])
    name_col = pick_column(frame, ["name", "名称", "SECURITY_NAME_ABBR"])
    score_col = pick_column(frame, ["综合得分", "TOTAL_SCORE", "score"])
    rank_col = pick_column(frame, ["目前排名", "RANK", "排名"])
    org_col = pick_column(frame, ["机构参与度", "ORG_PARTICIPATE"])
    focus_col = pick_column(frame, ["关注指数", "FOCUS"])
    text_col = pick_column(frame, ["诊断", "评论", "COMMENT", "主力成本"])
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
    title_col = pick_column(frame, ["title", "新闻标题", "标题"])
    url_col = pick_column(frame, ["url", "新闻链接", "链接"])
    time_col = pick_column(frame, ["time", "发布时间", "时间", "日期"])
    media_col = pick_column(frame, ["media", "文章来源", "来源", "source"])
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
            result["eastmoney"]["comment_note"] = "本次股票池在千股千评里未命中（新股常见），改为展示得分前列个股"
        result["eastmoney"]["comment"] = rows
        result["eastmoney"]["comment_source"] = comment_source
    else:
        result["eastmoney"]["comment"] = []
        result["eastmoney"]["comment_note"] = "千股千评接口不可得"

    hot_df, hot_source = try_chain(
        ctx,
        "em_hot",
        [
            ("akshare:stock_hot_rank_em", lambda: ctx.ak.hot_rank()),
            ("eastmoney:clist(成交额榜)", lambda: em.most_active(ctx.http, top=30)),
        ],
    )
    if hot_df is not None:
        frame = _normalize_spot(pd.DataFrame(hot_df))
        if not len(frame):
            frame = pd.DataFrame(hot_df)
        ctx.store.write_csv(pd.DataFrame(hot_df), "sentiment/em_hot_raw.csv")
        rank_col = pick_column(pd.DataFrame(hot_df), ["当前排名", "排名", "rank"])
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
            "东财人气榜需 POST 接口，akshare 不可用时以成交额活跃度榜作公开替代"
            if hot_source and hot_source.startswith("eastmoney")
            else None
        )

    news_map: dict[str, list[dict[str, Any]]] = {}
    for entry in list(universe)[: max(0, cfg.news_limit)]:
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
        result["ths"]["news_note"] = "同花顺快讯接口不可得（部分页面需 hexin-v Cookie）"

    boards_df, boards_source = try_chain(
        ctx,
        "ths_boards",
        [
            ("akshare:stock_hot_rank_wc", lambda: ctx.ak.ths_hot_rank()),
            ("ths:q.10jqka概念榜", lambda: ths.concept_rank(ctx.http, pages=1)),
        ],
    )
    if boards_df is not None:
        frame = pd.DataFrame(boards_df)
        ctx.store.write_csv(frame, "sentiment/ths_boards.csv")
        board_col = pick_column(frame, ["board", "板块", "概念名称", "股票名称", "name", "名称"])
        pct_col = pick_column(frame, ["pct_chg", "涨跌幅", "涨幅"])
        rank_col = pick_column(frame, ["rank", "排名", "序号"])
        detail_col = pick_column(frame, ["detail", "备注", "领涨股", "流入资金"])
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
        result["ths"]["boards_note"] = "同花顺热门板块榜不可得"

    result["notes"] = [
        "资讯与评论为第三方平台内容原文链接，未做情感加工，不代表本项目观点",
    ]
    return result


# ---------------- 报告组装 ----------------
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
            "title": f"A 股新股报告：近一年上市新股（{cfg.start_dash} ~ {cfg.end_dash}）",
            "detail_dir": "stocks",
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


def write_stock_pages(store: Store, payload: Mapping[str, Any], sections: Mapping[str, Any]) -> list[str]:
    """每只新股单独出一页明细。

    近一年新股常常 200~300 只，若全部图表塞进单页，HTML 会到几十 MB；
    因此总览页只留汇总表，明细按代码拆到 ``stocks/<代码>.html``。
    """
    new_map = {str(item.get("code")): item for item in (sections.get("new_stocks") or []) if item.get("code")}
    profile_map = {str(item.get("code")): item for item in (sections.get("profiles") or []) if item.get("code")}
    ordered = list(new_map) + [code for code in profile_map if code not in new_map]
    paths: list[str] = []
    for code in ordered:
        page = render_stock_page(payload, new_map.get(code), profile_map.get(code))
        paths.append(str(store.write_text(page, f"stocks/{code}.html")))
    return paths


def run(config: Config) -> dict[str, Any]:
    """执行一次完整采集并生成报告（只采集新股）。"""
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
        store.record("akshare", status="fallback", detail=f"akshare 不可用，已切换到公开接口兜底：{ak.import_error}")

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
        result["stock_pages"] = write_stock_pages(store, payload, sections)

    result["manifest"] = store.save_manifest(config.summary(), ak.info())
    result["events"] = store.events
    result["universe"] = universe
    return result


def self_check(config: Config) -> dict[str, Any]:
    """轻量自检：逐个探测关键能力，不写报告，便于首次部署验证。"""
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
            "指数日线",
            [
                ("akshare:index_hist", lambda: ak.index_hist(spec.code, spec.ak_symbol, config.start_ymd, config.end_ymd)),
                ("eastmoney:kline", lambda: em.kline(http, spec.secid, config.start_ymd, config.end_ymd, fqt=0)),
            ],
        ),
        (
            "全市场快照",
            [
                ("akshare:stock_zh_a_spot_em", lambda: _normalize_spot(ak.spot_all())),
                ("eastmoney:clist", lambda: _normalize_spot(em.spot_all(http))),
            ],
        ),
        (
            "新股名单",
            [("akshare:stock_xgsglb_em", lambda: ak.ipo_list())],
        ),
        (
            "同花顺快讯",
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
