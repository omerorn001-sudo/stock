"""图表 / 落盘 / 报告渲染的离线测试（不请求网络）。"""

from __future__ import annotations

import json

import pandas as pd

from ashare_yearly import charts
from ashare_yearly import report
from ashare_yearly.demo import demo_payload
from ashare_yearly.store import Store


def test_line_chart_outputs_svg():
    svg = charts.line_chart([("2026-09-08", 10.0), ("2026-09-09", 11.0)], title="t")
    assert svg.startswith("<svg")
    assert "</svg>" in svg


def test_charts_handle_empty_input():
    for svg in (
        charts.line_chart([]),
        charts.multi_line_chart({}),
        charts.candle_chart([]),
        charts.intraday_chart([]),
        charts.empty_chart("无数据"),
    ):
        assert svg.startswith("<svg")


def test_candle_and_intraday_render():
    bars = [
        {"date": "2026-09-08", "open": 10.0, "high": 10.8, "low": 9.9, "close": 10.5, "volume": 1000},
        {"date": "2026-09-09", "open": 10.5, "high": 11.2, "low": 10.4, "close": 11.0, "volume": 2000},
    ]
    assert "<svg" in charts.candle_chart(bars, title="k")
    assert "<svg" in charts.intraday_chart([("2026-09-09 09:31", 10.1)], 10.0)


def test_store_records_and_writes(tmp_path):
    store = Store(out_dir=tmp_path, data_dir=tmp_path / "data")
    store.record("index:000001", status="ok", source="akshare", rows=240)
    store.record("holders:301999", status="missing", detail="接口无数据")
    store.record("news:600000", status="fallback", source="eastmoney")

    csv_path = store.write_csv(pd.DataFrame({"date": ["2026-09-09"], "close": [10.0]}), "index/000001.csv")
    json_path = store.write_json({"a": 1}, "payload.json")
    html_path = store.write_text("<html></html>", "index.html")
    manifest_path = store.save_manifest({"区间": "x"}, {"available": False})

    assert csv_path.exists() and json_path.exists() and html_path.exists()
    assert len(store.failures()) == 1
    assert len(store.fallbacks()) == 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["events"]
    assert manifest["config"]["区间"] == "x"


def test_report_missing_values_use_placeholder():
    assert report.fmt_num(None) == report.MISSING
    assert report.fmt_num("-") == report.MISSING
    assert report.MISSING in report.fmt_pct(None)
    assert report.fmt_money(None) == report.MISSING
    assert report.fmt_money(1234567890.0) == "12.35亿元"
    assert report.fmt_shares(12340000) == "1,234.00万股"
    assert "up" in report.fmt_pct(1.5)
    assert "down" in report.fmt_pct(-1.5)
    assert report.esc("<script>") == "&lt;script&gt;"


def test_render_report_on_empty_payload():
    html = report.render_report({})
    assert html.startswith("<!DOCTYPE html>")
    assert report.MISSING in html


def test_render_report_on_demo_payload():
    payload = demo_payload()
    html = report.render_report(payload)
    for anchor in ('id="index"', 'id="new"', 'id="profile"', 'id="sector"', 'id="sentiment"', 'id="sources"'):
        assert anchor in html
    assert "演示新股" in html
    assert 'href="stocks/301999.html"' in html  # 总览页链到明细页
    assert "股东名称" not in html  # 股东明细已拆到单股页面
    assert html.count("<svg") >= 3
    assert report.MISSING in html  # 缺失字段仍然用占位符


def test_render_stock_page_has_full_detail():
    payload = demo_payload()
    stock = (payload.get("new_stocks") or [])[0]
    profile = next(p for p in payload["profiles"] if p["code"] == stock["code"])
    html = report.render_stock_page(payload, stock, profile)
    assert html.startswith("<!DOCTYPE html>")
    assert "前十大流通股东" in html
    assert "主营业务" in html
    assert '<a href="../index.html">' in html
    assert html.count("<svg") >= 3

    empty = report.render_stock_page(payload, {"code": "000000", "name": "缺数据"}, None)
    assert report.MISSING in empty  # 没有数据时只标占位符，不编造
