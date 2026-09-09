"""DataFrame 归一化工具。

各数据源（akshare / 东方财富 / 腾讯 / 同花顺）列名各异，这里统一成一套
规范列名，后续图表与报告只依赖规范列。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Sequence

import pandas as pd

OHLC_COLUMNS: tuple[str, ...] = (
    "date",
    "open",
    "close",
    "high",
    "low",
    "volume",
    "amount",
    "amplitude",
    "pct_chg",
    "change",
    "turnover",
)

MINUTE_COLUMNS: tuple[str, ...] = (
    "time",
    "open",
    "close",
    "high",
    "low",
    "volume",
    "amount",
    "avg",
)

_ALIASES: dict[str, str] = {
    # 日期/时间
    "日期": "date",
    "交易日期": "date",
    "date": "date",
    "trade_date": "date",
    "时间": "time",
    "datetime": "time",
    "day": "date",
    # 价格
    "开盘": "open",
    "开盘价": "open",
    "open": "open",
    "收盘": "close",
    "收盘价": "close",
    "close": "close",
    "最新价": "close",
    "最高": "high",
    "最高价": "high",
    "high": "high",
    "最低": "low",
    "最低价": "low",
    "low": "low",
    "今开": "open",
    "昨收": "pre_close",
    "均价": "avg",
    "price": "close",
    # 量额
    "成交量": "volume",
    "volume": "volume",
    "vol": "volume",
    "成交额": "amount",
    "amount": "amount",
    "turnover_amount": "amount",
    # 涨跌与换手
    "振幅": "amplitude",
    "涨跌幅": "pct_chg",
    "pct_chg": "pct_chg",
    "涨跌额": "change",
    "change": "change",
    "换手率": "turnover",
    "turnover": "turnover",
    "turnoverratio": "turnover",
}

_NUMERIC = {
    "open",
    "close",
    "high",
    "low",
    "pre_close",
    "avg",
    "volume",
    "amount",
    "amplitude",
    "pct_chg",
    "change",
    "turnover",
}


def safe_float(value: Any) -> float | None:
    """尽力转 float；``-``、``None``、空串、NaN 一律返回 None（不编造数值）。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
        return float(value)
    text = str(value).strip().replace(",", "").replace("%", "")
    if text in {"", "-", "--", "—", "None", "nan", "NaN", "null"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def ymd(value: Any) -> str:
    """把日期类值格式化成 ``YYYY-MM-DD``（解析失败则原样返回字符串）。"""
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if not text:
        return ""
    digits = text.replace("-", "").replace("/", "").replace(".", "")[:8]
    if len(digits) == 8 and digits.isdigit():
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return text[:19]


def rename_columns(df: pd.DataFrame) -> pd.DataFrame:
    mapping = {}
    for col in df.columns:
        key = str(col).strip()
        canon = _ALIASES.get(key) or _ALIASES.get(key.lower())
        if canon and canon not in mapping.values():
            mapping[col] = canon
    return df.rename(columns=mapping)


def _coerce(df: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    out = df.copy()
    for col in columns:
        if col in out.columns and col in _NUMERIC:
            out[col] = out[col].map(safe_float)
    return out


def normalize_ohlc(df: pd.DataFrame | None) -> pd.DataFrame:
    """归一化日/周 K 线表，返回按日期升序的规范列 DataFrame。"""
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=list(OHLC_COLUMNS))
    out = rename_columns(pd.DataFrame(df))
    if "date" not in out.columns and "time" in out.columns:
        out["date"] = out["time"]
    if "date" not in out.columns:
        out = out.reset_index().rename(columns={out.index.name or "index": "date"})
    out["date"] = out["date"].map(ymd)
    out = _coerce(out, out.columns)
    keep = [c for c in OHLC_COLUMNS if c in out.columns]
    extra = [c for c in ("pre_close", "avg") if c in out.columns]
    out = out[keep + extra]
    out = out[out["date"].astype(bool)]
    out = out.dropna(subset=[c for c in ("close",) if c in out.columns])
    return out.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)


def normalize_minute(df: pd.DataFrame | None) -> pd.DataFrame:
    """归一化分钟/分时表，返回按时间升序的规范列 DataFrame。"""
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=list(MINUTE_COLUMNS))
    out = rename_columns(pd.DataFrame(df))
    if "time" not in out.columns and "date" in out.columns:
        out = out.rename(columns={"date": "time"})
    if "time" not in out.columns:
        return pd.DataFrame(columns=list(MINUTE_COLUMNS))
    out["time"] = out["time"].map(lambda v: str(v).strip()[:19])
    out = _coerce(out, out.columns)
    if "close" not in out.columns and "avg" in out.columns:
        out["close"] = out["avg"]
    keep = [c for c in MINUTE_COLUMNS if c in out.columns]
    out = out[keep]
    out = out[out["time"].astype(bool)]
    if "close" in out.columns:
        out = out.dropna(subset=["close"])
    return out.sort_values("time").reset_index(drop=True)


def clip_range(df: pd.DataFrame, start: str | None = None, end: str | None = None, column: str = "date") -> pd.DataFrame:
    """按 ``YYYY-MM-DD`` 字符串区间裁剪（含端点）。"""
    if df is None or len(df) == 0 or column not in df.columns:
        return df if df is not None else pd.DataFrame()
    out = df
    if start:
        out = out[out[column] >= start]
    if end:
        out = out[out[column] <= end]
    return out.reset_index(drop=True)


def first_n_rows(df: pd.DataFrame, n: int) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return pd.DataFrame()
    return df.head(n).reset_index(drop=True)


def records(df: pd.DataFrame | None) -> list[dict[str, Any]]:
    if df is None or len(df) == 0:
        return []
    return [
        {k: (None if pd.isna(v) else v) for k, v in row.items()}
        for row in pd.DataFrame(df).to_dict(orient="records")
    ]


def series_points(df: pd.DataFrame, value_column: str = "close", label_column: str = "date") -> list[tuple[str, float]]:
    """抽取 (标签, 数值) 序列，供图表使用。"""
    if df is None or len(df) == 0 or value_column not in df.columns:
        return []
    points: list[tuple[str, float]] = []
    for _, row in pd.DataFrame(df).iterrows():
        value = safe_float(row.get(value_column))
        if value is None:
            continue
        points.append((str(row.get(label_column, "")), value))
    return points


def period_return(df: pd.DataFrame, column: str = "close") -> float | None:
    """区间涨跌幅（%）。数据不足返回 None。"""
    points = series_points(df, column)
    if len(points) < 2:
        return None
    first, last = points[0][1], points[-1][1]
    if not first:
        return None
    return round((last / first - 1) * 100, 2)


def pick_column(df: pd.DataFrame, names: Iterable[str]) -> str | None:
    """返回第一个存在的列名（支持模糊包含匹配）。"""
    if df is None or len(getattr(df, "columns", [])) == 0:
        return None
    columns = [str(c) for c in df.columns]
    for name in names:
        if name in columns:
            return name
    for name in names:
        for col in columns:
            if name in col:
                return col
    return None


def row_value(row: Any, names: Iterable[str]) -> Any:
    """从 dict / Series 里按候选键取第一个非空值（支持包含匹配）。"""
    if row is None:
        return None
    data = dict(row)
    for name in names:
        if name in data and data[name] is not None:
            return data[name]
    for name in names:
        for key, value in data.items():
            if name in str(key) and value is not None:
                return value
    return None
