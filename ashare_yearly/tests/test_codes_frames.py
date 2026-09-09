"""代码归一与表格工具的离线测试。"""

from __future__ import annotations

import pandas as pd
import pytest

from ashare_yearly import codes
from ashare_yearly import frames


def test_normalize_accepts_common_formats():
    assert codes.normalize("600519") == "600519"
    assert codes.normalize("sh600519") == "600519"
    assert codes.normalize("600519.SH") == "600519"
    assert codes.normalize(" 300750 ") == "300750"


def test_normalize_rejects_garbage():
    with pytest.raises(codes.CodeError):
        codes.normalize("abc")


def test_market_secid_and_board():
    assert codes.market("600519") == "SH"
    assert codes.market("000001") == "SZ"
    assert codes.market("899050") == "BJ"
    assert codes.secid("600519") == "1.600519"
    assert codes.secid("300750") == "0.300750"
    assert codes.prefixed("600519") == "sh600519"
    assert codes.dotted("600519") == "600519.SH"
    assert codes.board("688111") == "科创板"
    assert codes.board("300750") == "创业板"
    assert codes.is_a_share("600519") is True


def test_safe_float_never_invents_values():
    assert frames.safe_float("12.5") == 12.5
    assert frames.safe_float("1,234.5") == 1234.5
    assert frames.safe_float("3.2%") == 3.2
    assert frames.safe_float("-") is None
    assert frames.safe_float("—") is None
    assert frames.safe_float(None) is None
    assert frames.safe_float("") is None


def test_ymd_formats():
    assert frames.ymd("20260909") == "2026-09-09"
    assert frames.ymd("2026/09/09") == "2026-09-09"
    assert frames.ymd(pd.Timestamp("2026-09-09")) == "2026-09-09"


def test_normalize_ohlc_from_chinese_columns():
    raw = pd.DataFrame(
        {
            "日期": ["2026-09-08", "2026-09-09"],
            "开盘": [10.0, 10.5],
            "收盘": [10.5, 11.0],
            "最高": [10.8, 11.2],
            "最低": [9.9, 10.4],
            "成交量": [1000, 2000],
            "涨跌幅": ["5.0", "4.76"],
        }
    )
    tidy = frames.normalize_ohlc(raw)
    assert list(tidy["date"]) == ["2026-09-08", "2026-09-09"]
    assert tidy["close"].tolist() == [10.5, 11.0]
    assert frames.period_return(tidy) == pytest.approx(4.76, abs=0.01)


def test_normalize_ohlc_handles_empty():
    assert len(frames.normalize_ohlc(None)) == 0
    assert len(frames.normalize_ohlc(pd.DataFrame())) == 0
    assert frames.period_return(pd.DataFrame()) is None


def test_normalize_minute_and_clip():
    raw = pd.DataFrame({"时间": ["2026-09-09 09:31", "2026-09-09 09:32"], "收盘": [10.1, 10.2]})
    tidy = frames.normalize_minute(raw)
    assert tidy["time"].iloc[0] == "2026-09-09 09:31"
    assert frames.series_points(tidy, "close", "time")[1] == ("2026-09-09 09:32", 10.2)


def test_clip_range_and_first_n():
    tidy = pd.DataFrame({"date": ["2026-01-01", "2026-06-01", "2026-09-01"], "close": [1.0, 2.0, 3.0]})
    clipped = frames.clip_range(tidy, "2026-05-01", "2026-08-01")
    assert clipped["date"].tolist() == ["2026-06-01"]
    assert len(frames.first_n_rows(tidy, 2)) == 2


def test_pick_column_and_row_value():
    tidy = pd.DataFrame({"股东名称": ["A"], "持股数": [100]})
    assert frames.pick_column(tidy, ["holder", "股东名称"]) == "股东名称"
    assert frames.pick_column(tidy, ["不存在"]) is None
    assert frames.row_value({"总市值": 10}, ["总市值"]) == 10
    assert frames.row_value({"a": None}, ["a"]) is None
