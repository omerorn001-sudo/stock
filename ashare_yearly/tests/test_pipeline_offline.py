"""编排层的离线测试：只验证兜底逻辑与数据归一，不请求网络。"""

from __future__ import annotations

from datetime import date

import pandas as pd

from ashare_yearly import main as cli
from ashare_yearly import pipeline
from ashare_yearly.config import Config
from ashare_yearly.netutil import Http
from ashare_yearly.sources import AkAdapter
from ashare_yearly.store import Store


def _context(tmp_path, **overrides) -> pipeline.Context:
    config = Config(
        end=date(2026, 9, 9),
        out_dir=tmp_path,
        cache_dir=tmp_path / ".cache",
        use_akshare=False,
        use_cache=False,
        **overrides,
    )
    return pipeline.Context(
        config=config,
        ak=AkAdapter(enabled=False),
        http=Http(min_interval=0.0, timeout=1.0, retries=1, cache_dir=None),
        store=Store(out_dir=tmp_path, data_dir=tmp_path / "data"),
    )


def test_config_period_and_steps(tmp_path):
    ctx = _context(tmp_path)
    cfg = ctx.config
    assert cfg.end_dash == "2026-09-09"
    assert cfg.end_ymd == "20260909"
    assert cfg.start_dash == "2025-09-09"
    assert cfg.enabled("index") is True
    assert cfg.enabled("不存在的步骤") is False
    assert cfg.summary()


def test_try_chain_uses_fallback_when_first_provider_fails(tmp_path):
    ctx = _context(tmp_path)

    def broken():
        raise RuntimeError("akshare 不可用")

    def working():
        return pd.DataFrame({"date": ["2026-09-09"], "close": [10.0]})

    result, source = pipeline.try_chain(ctx, "demo", [("akshare:x", broken), ("eastmoney:y", working)])
    assert source == "eastmoney:y"
    assert len(result) == 1
    event = ctx.store.events[-1]
    assert event["status"] == "fallback"
    assert "akshare 不可用" in (event.get("detail") or "")


def test_try_chain_records_missing_when_all_fail(tmp_path):
    ctx = _context(tmp_path)
    result, source = pipeline.try_chain(
        ctx,
        "demo",
        [("a", lambda: None), ("b", lambda: pd.DataFrame())],
    )
    assert result is None and source is None
    assert ctx.store.events[-1]["status"] == "missing"
    assert len(ctx.store.failures()) == 1


def test_try_chain_first_provider_ok_is_not_fallback(tmp_path):
    ctx = _context(tmp_path)
    result, source = pipeline.try_chain(ctx, "demo", [("akshare:x", lambda: [1, 2, 3])])
    assert result == [1, 2, 3] and source == "akshare:x"
    assert ctx.store.events[-1]["status"] == "ok"
    assert ctx.store.fallbacks() == []


def test_normalize_spot_handles_akshare_columns():
    raw = pd.DataFrame(
        {
            "代码": ["600519", "300750"],
            "名称": ["A", "B"],
            "最新价": ["1500.5", "-"],
            "涨跌幅": [1.2, -0.5],
            "换手率": [0.5, 1.5],
            "市盈率-动态": ["25.3", "-"],
            "总市值": [1e12, 8e11],
            "流通市值": [9e11, 7e11],
        }
    )
    tidy = pipeline._normalize_spot(raw)
    assert tidy["code"].tolist() == ["600519", "300750"]
    assert tidy["price"].tolist()[0] == 1500.5
    # “-” 不会被编造成 0，而是空值（pandas 列内会呈现为 NaN）
    assert pd.isna(tidy["price"].tolist()[1])
    assert pd.isna(tidy["pe_dynamic"].tolist()[1])


def test_normalize_spot_handles_eastmoney_columns():
    raw = pd.DataFrame(
        {
            "code": ["688111"],
            "name": ["C"],
            "price": [88.8],
            "list_date": ["20260115"],
            "industry": ["软件开发"],
            "pe_static": [55.0],
        }
    )
    tidy = pipeline._normalize_spot(raw)
    assert tidy["list_date"].iloc[0] == "2026-01-15"
    assert tidy["industry"].iloc[0] == "软件开发"


def test_normalize_spot_without_code_column_returns_empty():
    assert len(pipeline._normalize_spot(pd.DataFrame({"x": [1]}))) == 0
    assert len(pipeline._normalize_spot(None)) == 0


def test_report_period_candidates_are_descending_and_past():
    periods = pipeline.report_period_candidates(date(2026, 9, 9), count=4)
    assert periods == ["20260630", "20260331", "20251231", "20250930"]


def test_strip_svg_removes_chart_payloads():
    payload = {
        "a": {"line_svg": "<svg/>", "keep": 1},
        "b": [{"intraday": {"svg": "<svg/>", "granularity": "1分钟"}}],
    }
    cleaned = pipeline._strip_svg(payload)
    assert cleaned == {"a": {"keep": 1}, "b": [{"intraday": {"granularity": "1分钟"}}]}


def test_build_payload_shape(tmp_path):
    ctx = _context(tmp_path)
    payload = pipeline.build_payload(ctx, {"indexes": [{"code": "000001"}]})
    assert payload["meta"]["config"]
    assert payload["meta"]["akshare"]["available"] is False
    for key in ("indexes", "new_stocks", "profiles", "sectors", "sentiment", "events"):
        assert key in payload


def test_cli_parses_codes_universe():
    args = cli.build_parser().parse_args(["--universe", "codes", "--codes", "600519,300750", "--steps", "index,report"])
    config = cli.config_from_args(args)
    assert config.codes == ("600519", "300750")
    assert config.steps == ("index", "report")
    assert config.enabled("new") is False


def test_cli_requires_codes_for_codes_universe(capsys):
    assert cli.main(["--universe", "codes"]) == 2


def test_cli_offline_demo_writes_html(tmp_path):
    assert cli.main(["--offline-demo", "--out", str(tmp_path)]) == 0
    html = (tmp_path / "demo.html").read_text(encoding="utf-8")
    assert "演示数据" in html
