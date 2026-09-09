"""采集编排：akshare 优先，不可用时兜底东方财富 / 同花顺，逐步落盘并渲染报告。

每个能力都走 ``try_chain``：按顺序尝试多个提供者，第一个成功的生效；
全部失败则记录 missing 事件，报告里用 — 占位，绝不编造数据。
"""

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

Provider = tuple[str, Callable[[], Any]]

SPOT_ALIASES: dict[str, str] = {
    "\u4ee3\u7801": "code",
    "\u80a1\u7968\u4ee3\u7801": "code",
    "code": "code",
    "\u540d\u79f0": "name",
    "\u80a1\u7968\u7b80\u79f0": "name",
    "\u80a1\u7968\u540d\u79f0": "name",
    "name": "name",
    "\u6700\u65b0\u4ef7": "price",
    "price": "price",
    "\u6da8\u8dcc\u5e45": "pct_chg",
    "pct_chg": "pct_chg",
    "\u6210\u4ea4\u91cf": "volume",
    "volume": "volume",
    "\u6210\u4ea4\u989d": "amount",
    "amount": "amount",
    "\u6362\u624b\u7387": "turnover",
    "turnover": "turnover",
    "\u5e02\u76c8\u7387-\u52a8\u6001": "pe_dynamic",
    "\u5e02\u76c8\u7387(\u52a8\u6001)": "pe_dynamic",
    "\u5e02\u76c8\u7387": "pe_dynamic",
    "pe_dynamic": "pe_dynamic",
    "\u5e02\u76c8\u7387-\u9759\u6001": "pe_static",
    "\u5e02\u76c8\u7387(\u9759)": "pe_static",
    "pe_static": "pe_static",
    "\u5e02\u76c8\u7387-TTM": "pe_ttm",
    "\u5e02\u76c8\u7387(TTM)": "pe_ttm",
    "pe_ttm": "pe_ttm",
    "\u5e02\u51c0\u7387": "pb",
    "pb": "pb",
    "\u603b\u5e02\u503c": "total_mv",
    "total_mv": "total_mv",
    "\u6d41\u901a\u5e02\u503c": "float_mv",
    "float_mv": "float_mv",
    "\u4e0a\u5e02\u65e5\u671f": "list_date",
    "\u4e0a\u5e02\u65f6\u95f4": "list_date",
    "list_date": "list_date",
    "\u6240\u5c5e\u884c\u4e1a": "industry",
    "\u884c\u4e1a": "industry",
    "industry": "industry",
}

NUMERIC_SPOT = (
    "price",
    "pct_chg",
    "volume",
    "amount",
    "turnover",
    "pe_dynamic",
    "pe_static",
    "pe_ttm",
    "pb",
    "total_mv",
    "float_mv",
)


@dataclass
class Context:
    config: Config
    ak: AkAdapter
    http: Http
    store: Store
    spot: pd.DataFrame | None = None
    spot_source: str | None = None
    board_map: dict[str, str] = field(default_factory=dict)


# ---------------- 基础工具 ----------------
def _size(result: Any) -> int | None:
    try:
        return len(result)
    except TypeError:
        return None


def _empty(result: Any) -> bool:
    if result is None:
        return True
    size = _size(result)
    return size == 0


def try_chain(ctx: Context, step: str, providers: Sequence[Provider]) -> tuple[Any, str | None]:
    """按顺序尝试提供者，返回 (结果, 实际数据源标签)；全部失败返回 (None, None)。"""
    errors: list[str] = []
    for index, (label, func) in enumerate(providers):
        try:
            result = func()
        except Exception as exc:  # noqa: BLE001 - 下游异常类型不可预知
            errors.append(f"{label}: {type(exc).__name__}: {exc}"[:240])
            continue
        if _empty(result):
            errors.append(f"{label}: \u7a7a\u6570\u636e")
            continue
        ctx.store.record(
            step,
            status="ok" if index == 0 else "fallback",
            source=label,
            rows=_size(result),
            detail=" | ".join(errors) or None,
        )
        return result, label
    ctx.store.record(step, status="missing", detail=" | ".join(errors) or "\u65e0\u53ef\u7528\u63d0\u4f9b\u8005")
    return None, None


def _normalize_spot(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return pd.DataFrame()
    out = pd.DataFrame(df)
    mapping: dict[Any, str] = {}
    for col in out.columns:
        canon = SPOT_ALIASES.get(str(col).strip())
        if canon and canon not in mapping.values():
            mapping[col] = canon
    out = out.rename(columns=mapping)
    if "code" not in out.columns:
        return pd.DataFrame()
    out["code"] = out["code"].astype(str).str.extract(r"(\\d{6})", expand=False)
    out = out.dropna(subset=["code"])
    for col in NUMERIC_SPOT:
        if col in out.columns:
            out[col] = out[col].map(safe_float)
    if "list_date" in out.columns:
        out["list_date"] = out["list_date"].map(lambda v: ymd(v) if v is not None else "")
    return out.reset_index(drop=True)


def load_spot(ctx: Context) -> pd.DataFrame | None:
    """全市场快照（只拉一次）。"""
    if ctx.spot is not None:
        return ctx.spot if len(ctx.spot) else None
    result, source = try_chain(
        ctx,
        "spot",
        [
            ("akshare:stock_zh_a_spot_em", lambda: _normalize_spot(ctx.ak.spot_all())),
            ("eastmoney:clist", lambda: _normalize_spot(em.spot_all(ctx.http))),
        ],
    )
    ctx.spot = result if result is not None else pd.DataFrame()
    ctx.spot_source = source
    if result is not None:
        ctx.store.write_csv(ctx.spot, "spot/spot.csv")
    return result


def spot_row(ctx: Context, code: str) -> dict[str, Any]:
    spot = load_spot(ctx)
    if spot is None or "code" not in spot.columns:
        return {}
    hit = spot[spot["code"] == codeutil.normalize(code)]
    if not len(hit):
        return {}
    return {k: (None if pd.isna(v) else v) for k, v in hit.iloc[0].to_dict().items()}


def report_period_candidates(end: date, count: int = 5) -> list[str]:
    """最近的几个报告期（``YYYYMMDD``，降序）。"""
    quarters = ((3, 31), (6, 30), (9, 30), (12, 31))
    candidates: list[date] = []
    for year in (end.year, end.year - 1, end.year - 2):
        for month, day in quarters:
            candidates.append(date(year, month, day))
    usable = sorted([c for c in candidates if c <= end], reverse=True)
    return [c.strftime("%Y%m%d") for c in usable[:count]]


# ---------------- 1. 指数 ----------------
def collect_indexes(ctx: Context) -> tuple[list[dict[str, Any]], str | None]:
    cfg = ctx.config
    items: list[dict[str, Any]] = []
    series: dict[str, list[tuple[str, float]]] = {}
    for spec in cfg.indexes:
        df, source = try_chain(
            ctx,
            f"index:{spec.code}",
            [
                (
                    f"akshare:index_hist({spec.code})",
                    lambda spec=spec: ctx.ak.index_hist(spec.code, spec.ak_symbol, cfg.start_ymd, cfg.end_ymd),
                ),
                (
                    f"eastmoney:kline({spec.secid})",
                    lambda spec=spec: em.kline(ctx.http, spec.secid, cfg.start_ymd, cfg.end_ymd, fqt=0),
                ),
            ],
        )
        if df is None:
            items.append(
                {
                    "code": spec.code,
                    "name": spec.name,
                    "source": None,
                    "rows": 0,
                    "note": "akshare \u4e0e\u4e1c\u8d22\u884c\u60c5\u63a5\u53e3\u5747\u672a\u8fd4\u56de\u6570\u636e",
                    "line_svg": charts.empty_chart(f"{spec.name} \u65e0\u884c\u60c5\u6570\u636e"),
                }
            )
            continue
        df = clip_range(df, cfg.start_dash, cfg.end_dash)
        ctx.store.write_csv(df, f"index/{spec.code}.csv")
        points = series_points(df)
        bars = records(df)
        item = {
            "code": spec.code,
            "name": spec.name,
            "source": source,
            "rows": len(df),
            "start": points[0][0] if points else None,
            "end": points[-1][0] if points else None,
            "pct_chg": period_return(df),
            "last_close": points[-1][1] if points else None,
            "high": safe_float(df["high"].max()) if "high" in df.columns and len(df) else None,
            "low": safe_float(df["low"].min()) if "low" in df.columns and len(df) else None,
            "line_svg": charts.line_chart(points, title=f"{spec.name} \u6536\u76d8\u8d70\u52bf"),
            "candle_svg": charts.candle_chart(bars[-120:], title=f"{spec.name} \u8fd1 120 \u4e2a\u4ea4\u6613\u65e5"),
        }
        items.append(item)
        if points:
            series[spec.name] = points
    compare = charts.multi_line_chart(series, title=f"{cfg.start_dash} ~ {cfg.end_dash}") if series else None
    return items, compare


# ---------------- 2. 新股 ----------------
def _ipo_frame(ctx: Context) -> pd.DataFrame | None:
    df, source = try_chain(
        ctx,
        "ipo_list",
        [
            ("akshare:stock_xgsglb_em", lambda: ctx.ak.ipo_list()),
            ("eastmoney:clist(list_date)", lambda: _normalize_spot(em.spot_all(ctx.http))),
        ],
    )
    if df is None:
        return None
    frame = pd.DataFrame(df)
    code_col = pick_column(frame, ["code", "\u80a1\u7968\u4ee3\u7801", "\u4ee3\u7801"])
    name_col = pick_column(frame, ["name", "\u80a1\u7968\u7b80\u79f0", "\u540d\u79f0", "\u80a1\u7968\u540d\u79f0"])
    date_col = pick_column(frame, ["list_date", "\u4e0a\u5e02\u65e5\u671f", "\u4e0a\u5e02\u65f6\u95f4"])
    if not code_col or not date_col:
        ctx.store.record("ipo_list", status="missing", source=source, detail="\u8fd4\u56de\u8868\u7f3a\u5c11\u4ee3\u7801\u6216\u4e0a\u5e02\u65e5\u671f\u5217")
        return None
    out = pd.DataFrame(
        {
            "code": frame[code_col].astype(str).str.extract(r"(\\d{6})", expand=False),
            "name": frame[name_col] if name_col else None,
            "list_date": frame[date_col].map(ymd),
        }
    ).dropna(subset=["code"])
    out = out[out["list_date"].astype(bool)]
    out["source"] = source
    return out.reset_index(drop=True)


def resolve_universe(ctx: Context) -> list[dict[str, Any]]:
    """确定本次采集的股票池，返回 ``[{code, name, list_date}]``。"""
    cfg = ctx.config
    limit = max(1, cfg.deep_limit)

    if cfg.universe == "codes":
        picked = []
        for raw in cfg.codes:
            try:
                code = codeutil.normalize(raw)
            except codeutil.CodeError as exc:
                ctx.store.record("universe", status="error", detail=str(exc))
                continue
            row = spot_row(ctx, code)
            picked.append(
                {"code": code, "name": row.get("name"), "list_date": row.get("list_date") or None}
            )
        ctx.store.record("universe", status="ok", source="cli:--codes", rows=len(picked))
        return picked[:limit]

    if cfg.universe == "new":
        ipo = _ipo_frame(ctx)
        if ipo is None or not len(ipo):
            ctx.store.record("universe", status="missing", detail="\u65e0\u6cd5\u83b7\u53d6\u65b0\u80a1\u4e0a\u5e02\u540d\u5355")
            return []
        recent = ipo[ipo["list_date"] >= cfg.start_dash]
        recent = recent[recent["list_date"] <= cfg.end_dash]
        recent = recent.sort_values("list_date", ascending=False).head(limit)
        ctx.store.write_csv(recent, "new/ipo_list.csv")
        ctx.store.record("universe", status="ok", source="ipo_list", rows=len(recent))
        return records(recent)

    spot = load_spot(ctx)
    if spot is None or not len(spot):
        ctx.store.record("universe", status="missing", detail="\u5feb\u7167\u4e0d\u53ef\u7528\uff0c\u65e0\u6cd5\u786e\u5b9a\u80a1\u7968\u6c60")
        return []
    frame = spot.copy()
    if cfg.universe == "active" and "amount" in frame.columns:
        frame = frame.sort_values("amount", ascending=False)
    frame = frame.head(limit)
    ctx.store.record("universe", status="ok", source=f"spot:{cfg.universe}", rows=len(frame))
    return [
        {"code": row.get("code"), "name": row.get("name"), "list_date": row.get("list_date") or None}
        for row in records(frame)
    ]


def _em_minute_day(ctx: Context, secid: str, day: str, klt: int) -> pd.DataFrame:
    ymd_compact = day.replace("-", "")
    df = em.kline(ctx.http, secid, ymd_compact, ymd_compact, klt=klt, fqt=0, limit=400)
    if "time" in df.columns:
        df = df[df["time"].astype(str).str.startswith(day)]
    return df.reset_index(drop=True)


def _em_trends_day(ctx: Context, secid: str, day: str) -> pd.DataFrame:
    df = em.trends(ctx.http, secid, ndays=5)
    if "time" in df.columns:
        df = df[df["time"].astype(str).str.startswith(day)]
    return df.reset_index(drop=True)


def _intraday(ctx: Context, code: str, name: str | None, day: str, prev_close: float | None) -> dict[str, Any]:
    secid = codeutil.secid(code)
    df, source = try_chain(
        ctx,
        f"intraday:{code}:{day}",
        [
            (
                "akshare:stock_zh_a_hist_min_em(1\u5206\u949f)",
                lambda: ctx.ak.stock_minute(code, f"{day} 09:15:00", f"{day} 15:00:00", period="1"),
            ),
            (
                "akshare:stock_zh_a_hist_min_em(5\u5206\u949f)",
                lambda: ctx.ak.stock_minute(code, f"{day} 09:15:00", f"{day} 15:00:00", period="5"),
            ),
            ("eastmoney:kline(klt=1)", lambda: _em_minute_day(ctx, secid, day, 1)),
            ("eastmoney:kline(klt=5)", lambda: _em_minute_day(ctx, secid, day, 5)),
            ("eastmoney:trends2", lambda: _em_trends_day(ctx, secid, day)),
        ],
    )
    if df is None or not len(df):
        return {
            "svg": charts.empty_chart(f"{day} \u5206\u65f6/\u5206\u949f\u6570\u636e\u4e0d\u53ef\u5f97"),
            "granularity": None,
            "note": "\u4e1c\u8d22\u5206\u65f6\u63a5\u53e3\u4ec5\u4fdd\u7559\u8fd1 5 \u4e2a\u4ea4\u6613\u65e5\u3001\u5206\u949f K \u7ebf\u4fdd\u7559\u671f\u4e5f\u6709\u9650\uff0c\u8f83\u65e9\u7684\u4e0a\u5e02\u9996\u65e5\u65e5\u5185\u6570\u636e\u901a\u5e38\u65e0\u6cd5\u56de\u6eaf",
        }
    ctx.store.write_csv(df, f"new/{code}/intraday-{day}.csv")
    points = series_points(df, "close", "time")
    return {
        "svg": charts.intraday_chart(points, prev_close, title=f"{name or code} {day} \u5206\u65f6"),
        "granularity": source,
        "note": None if len(points) > 30 else "\u5206\u65f6\u70b9\u6570\u8f83\u5c11\uff0c\u53ef\u80fd\u4e3a\u63a5\u53e3\u964d\u7ea7\u8fd4\u56de",
    }


def collect_new_stocks(ctx: Context, universe: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    cfg = ctx.config
    items: list[dict[str, Any]] = []
    for entry in universe:
        code = entry.get("code")
        if not code:
            continue
        name = entry.get("name")
        list_date = entry.get("list_date") or None
        notes: list[str] = []
        start_ymd = (list_date or cfg.start_dash).replace("-", "")
        secid = codeutil.secid(code)
        df, source = try_chain(
            ctx,
            f"new_hist:{code}",
            [
                (
                    "akshare:stock_zh_a_hist",
                    lambda code=code: ctx.ak.stock_hist(code, start_ymd, cfg.end_ymd, adjust=cfg.adjust),
                ),
                (
                    "eastmoney:kline",
                    lambda secid=secid: em.kline(ctx.http, secid, start_ymd, cfg.end_ymd, fqt=1),
                ),
            ],
        )
        if df is None:
            items.append(
                {
                    "code": code,
                    "name": name,
                    "list_date": list_date,
                    "source": None,
                    "since_ipo": {},
                    "first_days": [],
                    "notes": ["akshare \u4e0e\u4e1c\u8d22\u65e5\u7ebf\u63a5\u53e3\u5747\u672a\u8fd4\u56de\u6570\u636e"],
                }
            )
            continue

        ctx.store.write_csv(df, f"new/{code}/daily.csv")
        points = series_points(df)
        bars = records(df)
        if not list_date and points:
            list_date = points[0][0]
            notes.append("\u4e0a\u5e02\u65e5\u671f\u7f3a\u5931\uff0c\u4ee5\u533a\u95f4\u5185\u9996\u4e2a\u4ea4\u6613\u65e5\u4ee3\u66ff")

        first_rows = records(first_n_rows(df, cfg.first_days))
        first_days: list[dict[str, Any]] = []
        prev_close: float | None = None
        for row in first_rows:
            day = str(row.get("date") or "")
            reference = prev_close if prev_close is not None else safe_float(row.get("open"))
            first_days.append(
                {
                    "date": day,
                    "open": safe_float(row.get("open")),
                    "high": safe_float(row.get("high")),
                    "low": safe_float(row.get("low")),
                    "close": safe_float(row.get("close")),
                    "pct_chg": safe_float(row.get("pct_chg")),
                    "turnover": safe_float(row.get("turnover")),
                    "volume": safe_float(row.get("volume")),
                    "amount": safe_float(row.get("amount")),
                    "intraday": _intraday(ctx, code, name, day, reference),
                }
            )
            prev_close = safe_float(row.get("close")) or prev_close

        items.append(
            {
                "code": code,
                "name": name,
                "list_date": list_date,
                "source": source,
                "since_ipo": {
                    "rows": len(df),
                    "pct_chg": period_return(df),
                    "first_close": points[0][1] if points else None,
                    "last_close": points[-1][1] if points else None,
                    "line_svg": charts.line_chart(points, title=f"{name or code} \u4e0a\u5e02\u81f3\u4eca\u6536\u76d8\u8d70\u52bf"),
                    "candle_svg": charts.candle_chart(bars, title=f"{name or code} \u4e0a\u5e02\u81f3\u4eca\u65e5 K"),
                },
                "first_days": first_days,
                "notes": notes,
            }
        )
    return items


# ---------------- 3. 个股画像 ----------------
def _individual_info(ctx: Context, code: str) -> dict[str, Any]:
    df, source = try_chain(
        ctx,
        f"individual_info:{code}",
        [("akshare:stock_individual_info_em", lambda: ctx.ak.individual_info(code))],
    )
    if df is None:
        return {}
    frame = pd.DataFrame(df)
    key_col = pick_column(frame, ["item", "\u9879\u76ee"])
    value_col = pick_column(frame, ["value", "\u503c"])
    if not key_col or not value_col:
        return {}
    info = {str(row[key_col]).strip(): row[value_col] for _, row in frame.iterrows()}
    info["_source"] = source
    return info


def _holders(ctx: Context, code: str) -> dict[str, Any]:
    cfg = ctx.config
    periods = report_period_candidates(cfg.end)
    providers: list[Provider] = []
    for period in periods[:3]:
        providers.append(
            (
                f"akshare:stock_gdfx_free_top_10_em({period})",
                lambda period=period: ctx.ak.free_top10_holders(code, period),
            )
        )
    providers.append(("akshare:stock_gdfx_free_top_10_em(latest)", lambda: ctx.ak.free_top10_holders(code)))
    providers.append(("eastmoney:F10 freeholders", lambda: em.free_top10_holders(ctx.http, code)[0]))

    df, source = try_chain(ctx, f"holders:{code}", providers)
    if df is None:
        return {
            "rows": [],
            "report_date": None,
            "source": None,
            "note": "akshare \u4e0e\u4e1c\u8d22 F10 \u80a1\u4e1c\u63a5\u53e3\u5747\u672a\u8fd4\u56de\u6570\u636e\uff08\u65b0\u80a1\u9996\u4efd\u62a5\u544a\u524d\u65e0\u80a1\u4e1c\u660e\u7ec6\u5c5e\u6b63\u5e38\uff09",
        }
    frame = pd.DataFrame(df)
    holder_col = pick_column(frame, ["holder", "\u80a1\u4e1c\u540d\u79f0", "\u80a1\u4e1c"])
    rank_col = pick_column(frame, ["rank", "\u540d\u6b21", "\u5e8f\u53f7"])
    shares_col = pick_column(frame, ["shares", "\u6301\u80a1\u6570", "\u6301\u80a1\u6570\u91cf"])
    ratio_col = pick_column(frame, ["ratio", "\u5360\u603b\u6d41\u901a\u80a1\u672c\u6301\u80a1\u6bd4\u4f8b", "\u5360\u6d41\u901a\u80a1\u6bd4\u4f8b", "\u6bd4\u4f8b"])
    change_col = pick_column(frame, ["change", "\u589e\u51cf", "\u53d8\u52a8"])
    type_col = pick_column(frame, ["holder_type", "\u80a1\u4e1c\u6027\u8d28", "\u80a1\u4efd\u7c7b\u578b"])
    date_col = pick_column(frame, ["end_date", "\u622a\u6b62\u65e5\u671f", "\u62a5\u544a\u671f"])

    rows = []
    for index, raw in enumerate(records(frame)[:10], start=1):
        rows.append(
            {
                "rank": raw.get(rank_col) if rank_col else index,
                "holder": raw.get(holder_col) if holder_col else None,
                "shares": safe_float(raw.get(shares_col)) if shares_col else None,
                "ratio": safe_float(raw.get(ratio_col)) if ratio_col else None,
                "change": raw.get(change_col) if change_col else None,
                "holder_type": raw.get(type_col) if type_col else None,
            }
        )
    ctx.store.write_csv(frame, f"profile/{code}/holders.csv")
    report_date = None
    if date_col and len(frame):
        report_date = ymd(frame.iloc[0][date_col])
    elif source and "(" in source and ")" in source:
        report_date = ymd(source[source.rfind("(") + 1 : source.rfind(")")])
    return {
        "rows": rows,
        "report_date": report_date,
        "source": source,
        "note": "\u6301\u80a1\u6570\u91cf\u5355\u4f4d\u6cbf\u7528\u6570\u636e\u6e90\u53e3\u5f84\uff08\u901a\u5e38\u4e3a\u80a1\uff09" if rows else None,
    }


def _main_business(ctx: Context, code: str, name: str | None) -> tuple[str | None, str | None]:
    df, source = try_chain(
        ctx,
        f"main_business:{code}",
        [
            ("akshare:stock_zyjs_ths", lambda: ctx.ak.main_business(code)),
            ("eastmoney:F10 business", lambda: pd.DataFrame([em.business_profile(ctx.http, code)])),
            ("ths:basic.10jqka", lambda: pd.DataFrame([ths.main_business(ctx.http, code)])),
        ],
    )
    if df is None:
        return None, None
    frame = pd.DataFrame(df)
    column = pick_column(frame, ["main_business", "\u4e3b\u8981\u4e1a\u52a1", "\u4e3b\u8425\u4e1a\u52a1", "\u7ecf\u8425\u8303\u56f4", "business_scope", "\u4e3b\u8425\u4ea7\u54c1", "\u4ea7\u54c1\u540d\u79f0"])
    if not column:
        return None, source
    values = [str(v).strip() for v in frame[column].tolist() if v is not None and str(v).strip()]
    if not values:
        return None, source
    text = values[0]
    if len(values) > 1 and len(text) < 40:
        text = "\uff1b".join(dict.fromkeys(values))[:600]
    return text[:1200], source
