"""离线演示：用合成数据验证图表与报告渲染链路（不联网）。

这里的数字全部是确定性合成的假数据，仅用于自检与样式预览，
报告标题会明确标注“演示数据”，避免与真实行情混淆。
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import charts
from .config import DEFAULT_OUT_DIR
from .report import render_report

DEMO_SOURCE = "demo:合成数据"


def trading_days(end: date, count: int) -> list[str]:
    days: list[str] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor.strftime("%Y-%m-%d"))
        cursor -= timedelta(days=1)
    return list(reversed(days))


def synthetic_bars(days: list[str], base: float, amplitude: float = 0.02, seed: float = 0.0) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    close = base
    for index, day in enumerate(days):
        drift = math.sin((index + seed) / 9.0) * amplitude + math.cos((index + seed) / 23.0) * amplitude / 2
        open_ = close
        close = round(max(0.5, open_ * (1 + drift / 3)), 2)
        bars.append(
            {
                "date": day,
                "open": round(open_, 2),
                "high": round(max(open_, close) * (1 + abs(drift) / 2), 2),
                "low": round(min(open_, close) * (1 - abs(drift) / 2), 2),
                "close": close,
                "volume": round(1000000 * (1 + abs(drift) * 8)),
                "amount": round(close * 1000000 * (1 + abs(drift) * 8)),
                "pct_chg": round((close / open_ - 1) * 100, 2) if open_ else None,
                "turnover": round(2 + abs(drift) * 60, 2),
            }
        )
    return bars


def points_of(bars: list[dict[str, Any]]) -> list[tuple[str, float]]:
    return [(bar["date"], bar["close"]) for bar in bars]


def intraday_points(day: str, base: float) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for minute in range(0, 240, 3):
        hour = 9 + (30 + minute) // 60
        mm = (30 + minute) % 60
        value = base * (1 + math.sin(minute / 27.0) * 0.012 + minute / 240 * 0.01)
        out.append((day + " " + f"{hour:02d}:{mm:02d}", round(value, 2)))
    return out


def _index_items(year_days: list[str]) -> tuple[list[dict[str, Any]], str]:
    items: list[dict[str, Any]] = []
    series: dict[str, list[tuple[str, float]]] = {}
    for code, name, base, seed in (("000001", "上证指数", 3100.0, 0.0), ("399006", "创业板指", 1900.0, 4.0)):
        bars = synthetic_bars(year_days, base, 0.018, seed)
        points = points_of(bars)
        items.append(
            {
                "code": code,
                "name": name,
                "source": DEMO_SOURCE,
                "rows": len(bars),
                "start": points[0][0],
                "end": points[-1][0],
                "pct_chg": round((points[-1][1] / points[0][1] - 1) * 100, 2),
                "last_close": points[-1][1],
                "high": max(bar["high"] for bar in bars),
                "low": min(bar["low"] for bar in bars),
                "line_svg": charts.line_chart(points, title=name + " 收盘走势（演示）"),
                "candle_svg": charts.candle_chart(bars[-90:], title=name + " 近 90 个交易日（演示）"),
                "note": "演示数据，非真实行情",
            }
        )
        series[name] = points
    return items, charts.multi_line_chart(series, title="相对走势（演示）")


def _new_stock_item(ipo_days: list[str], ipo_bars: list[dict[str, Any]]) -> dict[str, Any]:
    first_days: list[dict[str, Any]] = []
    prev_close: float | None = None
    for bar in ipo_bars[:7]:
        reference = prev_close if prev_close is not None else bar["open"]
        day = dict(bar)
        day["intraday"] = {
            "svg": charts.intraday_chart(
                intraday_points(bar["date"], bar["close"]),
                reference,
                title="演示新股 " + bar["date"] + " 分时",
            ),
            "granularity": "demo:1分钟",
            "note": "演示数据",
        }
        first_days.append(day)
        prev_close = bar["close"]
    return {
        "code": "301999",
        "name": "演示新股",
        "list_date": ipo_days[0],
        "source": DEMO_SOURCE,
        "since_ipo": {
            "rows": len(ipo_bars),
            "pct_chg": round((ipo_bars[-1]["close"] / ipo_bars[0]["close"] - 1) * 100, 2),
            "first_close": ipo_bars[0]["close"],
            "last_close": ipo_bars[-1]["close"],
            "line_svg": charts.line_chart(points_of(ipo_bars), title="上市至今收盘走势（演示）"),
            "candle_svg": charts.candle_chart(ipo_bars, title="上市至今日 K（演示）"),
        },
        "first_days": first_days,
        "notes": ["演示数据；真实运行时较早的上市首日分时可能无法回溯"],
    }


def _profiles(ipo_days: list[str], ipo_bars: list[dict[str, Any]], year_days: list[str]) -> list[dict[str, Any]]:
    valuation = synthetic_bars(year_days, 32.0, 0.01, 3.0)
    holders = [
        {
            "rank": i,
            "holder": "演示股东" + str(i) + "号",
            "shares": 1000000 * (11 - i),
            "ratio": round(9.5 - i * 0.7, 2),
            "change": "不变" if i % 2 else "新进",
            "holder_type": "境内自然人" if i % 3 else "基金",
        }
        for i in range(1, 11)
    ]
    return [
        {
            "code": "301999",
            "name": "演示新股",
            "board": "创业板",
            "industry": "专用设备",
            "list_date": ipo_days[0],
            "price": ipo_bars[-1]["close"],
            "pct_chg": ipo_bars[-1]["pct_chg"],
            "total_mv": 12345678900.0,
            "float_mv": 3456789000.0,
            "pe_static": 45.6,
            "pe_dynamic": 38.2,
            "pe_ttm": 41.7,
            "pb": 4.12,
            "turnover": 12.34,
            "main_business": "演示用主营业务描述：专用设备的研发、生产与销售（合成文本）。",
            "main_business_source": DEMO_SOURCE,
            "holders": {"report_date": "2026-06-30", "source": DEMO_SOURCE, "rows": holders, "note": "演示数据"},
            "valuation_svg": charts.line_chart(
                points_of(valuation), title="市盈率（TTM）走势（演示）", color="#2f6fd0", area=False
            ),
            "source": DEMO_SOURCE,
            "notes": [],
        },
        {
            "code": "600000",
            "name": "演示主板股",
            "board": "沪市主板",
            "industry": "银行",
            "list_date": "1999-11-10",
            "price": 9.87,
            "pct_chg": -0.81,
            "total_mv": 289000000000.0,
            "float_mv": 280000000000.0,
            "pe_static": 5.4,
            "pe_dynamic": 5.1,
            "pe_ttm": 5.2,
            "pb": 0.51,
            "turnover": 0.36,
            "main_business": None,
            "main_business_source": None,
            "holders": {"rows": [], "report_date": None, "source": None, "note": "演示：接口不可得时的展示效果"},
            "valuation_svg": None,
            "source": DEMO_SOURCE,
            "notes": ["演示：展示字段缺失时的占位效果"],
        },
    ]


def _sentiment() -> dict[str, Any]:
    return {
        "eastmoney": {
            "comment": [
                {
                    "code": "301999",
                    "name": "演示新股",
                    "score": 68.5,
                    "rank": 412,
                    "org_participation": 32.1,
                    "focus": 78.0,
                    "comment": "演示：千股千评文本字段",
                }
            ],
            "comment_source": DEMO_SOURCE,
            "hot": [
                {
                    "code": "301999",
                    "name": "演示新股",
                    "pct_chg": 4.21,
                    "amount": 1234000000.0,
                    "turnover": 12.3,
                    "rank": 5,
                }
            ],
            "news": {
                "演示新股 301999": [
                    {
                        "title": "演示：东方财富资讯标题示例",
                        "url": "https://www.eastmoney.com/",
                        "time": "2026-09-08 09:30",
                        "media": "东方财富网",
                    }
                ]
            },
        },
        "ths": {
            "news": [
                {
                    "title": "演示：同花顺快讯标题示例",
                    "url": "https://news.10jqka.com.cn/",
                    "time": "2026-09-08 10:05",
                    "media": "同花顺",
                }
            ],
            "boards": [{"board": "演示概念", "pct_chg": "3.21%", "rank": 1, "detail": "演示领涨股"}],
        },
        "notes": ["演示数据，不代表真实行情与舆论"],
    }


def demo_payload(end: date | None = None) -> dict[str, Any]:
    """构造一份结构与真实采集完全一致的演示 payload。"""
    end = end or date.today()
    year_days = trading_days(end, 240)
    ipo_days = trading_days(end, 40)
    ipo_bars = synthetic_bars(ipo_days, 28.0, 0.05, 7.0)
    indexes, index_compare = _index_items(year_days)
    sector_bars = synthetic_bars(year_days, 1200.0, 0.02, 11.0)
    sectors = [
        {
            "name": "专用设备",
            "code": "BK0000",
            "source": DEMO_SOURCE,
            "rows": len(sector_bars),
            "pct_chg": round((sector_bars[-1]["close"] / sector_bars[0]["close"] - 1) * 100, 2),
            "last_close": sector_bars[-1]["close"],
            "members": ["301999"],
            "line_svg": charts.line_chart(points_of(sector_bars), title="专用设备近一年（演示）"),
            "note": "演示数据",
        }
    ]
    return {
        "meta": {
            "title": "A 股近一年详细信息报告（离线演示数据，非真实行情）",
            "generated_at": end.strftime("%Y-%m-%d") + " 00:00:00",
            "config": {
                "区间": year_days[0] + " ~ " + year_days[-1],
                "股票池": "demo",
                "执行步骤": "index,new,profile,sector,sentiment,report",
                "说明": "全部数字为合成演示数据",
            },
            "akshare": {"available": False, "version": None, "import_error": "离线演示未加载 akshare"},
        },
        "indexes": indexes,
        "index_compare_svg": index_compare,
        "new_stocks": [_new_stock_item(ipo_days, ipo_bars)],
        "profiles": _profiles(ipo_days, ipo_bars, year_days),
        "sectors": sectors,
        "sector_compare_svg": None,
        "sentiment": _sentiment(),
        "events": [
            {"step": "demo", "status": "fallback", "source": DEMO_SOURCE, "rows": 1, "detail": "离线演示，未请求任何接口"},
            {"step": "demo:holders:600000", "status": "missing", "source": None, "rows": 0, "detail": "演示缺失事件展示"},
        ],
    }


def render_demo(out_dir: Path | str | None = None, end: date | None = None) -> Path:
    """渲染演示报告到 ``out_dir/demo.html``，返回文件路径。"""
    target = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
    target.mkdir(parents=True, exist_ok=True)
    path = target / "demo.html"
    path.write_text(render_report(demo_payload(end)), encoding="utf-8")
    return path
