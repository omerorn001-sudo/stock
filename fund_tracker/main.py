"""Run from repository root: python -m fund_tracker.main"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .core import PERIODS, iso_date, latest_holdings, previous_session, profile_fields, rank_funds, summarize_holdings
from .providers import Provider, bounded_http, dump
from .report import write_reports

SHANGHAI = ZoneInfo("Asia/Shanghai")


def demo_report(top_n=50):
    """Synthetic fixtures only. Never use these as real market observations."""
    now = datetime.now(SHANGHAI)
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    rows = [{"基金代码": str(900001 + i), "基金简称": f"演示基金 {i+1:02d}（非真实）",
             "日期": yesterday, "单位净值": round(1 + i / 100, 4), "日增长率": round((i % 7 - 3) / 10, 2),
             **{v: round((60 - i) / (j + 1), 2) for j, v in enumerate(PERIODS.values())}}
            for i in range(60)]
    funds, meta = rank_funds(rows, top_n, now.date(), yesterday)
    for f in funds:
        f["profile"] = {"manager": "演示经理", "inception_date": "2020-01-01", "fund_type": "演示股票型",
                        "aum_raw": "12.50亿元（测试值）", "aum_as_of": "2026-06-30", "missing_fields": []}
        hs, info = latest_holdings([
            {"股票代码": "600001", "股票名称": "演示股票甲", "占净值比例": 8.5, "季度": "2026年2季度", "持股数": 12, "持仓市值": 120},
            {"股票代码": "00700", "股票名称": "演示股票乙", "占净值比例": 5.2, "季度": "2026年2季度", "持股数": 7, "持仓市值": 50}], now.date())
        for h in hs:
            h.update({"industry": "演示制造业" if h["market"] == "SH" else None,
                      "concepts": ["演示科技主题"] if h["market"] == "SH" else [], "market_tags": [],
                      "classification_status": "synthetic"})
        f.update({"holdings": hs, "holdings_info": info, "analysis": summarize_holdings(hs)})
    meta.update({"demo": True, "status": "DEMO / 非真实数据", "collected_at": now.isoformat(), "category": "测试样例",
                 "concept_coverage": {"boards_completed": 1, "boards_total": 1, "complete": True}})
    return {"metadata": meta, "funds": funds, "sources": {}, "warnings": []}


def load_overrides(path, today):
    if not path:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Overrides must be a security_id -> object map")
    for key, value in data.items():
        if not isinstance(value, dict) or ":" not in key:
            raise ValueError(f"Invalid override: {key}")
        stamp = iso_date(value.get("as_of"))
        if not value.get("source_url") or not stamp or stamp > today.isoformat():
            raise ValueError(f"Override requires source_url and non-future as_of: {key}")
        if not isinstance(value.get("concepts", []), list) or not all(isinstance(x, str) for x in value.get("concepts", [])):
            raise ValueError("Override concepts must be a list of strings")
    return data


def collect(provider, category, top_n, max_age, override_path=None):
    now = datetime.now(SHANGHAI)
    today = now.date()
    previous = None
    cal, _ = provider.attempt("calendar", "akshare:tool_trade_date_hist_sina",
        "https://finance.sina.com.cn/realstock/company/klc_td_sh.txt",
        lambda: provider.ak("tool_trade_date_hist_sina"), 24)
    if cal:
        try:
            previous = previous_session(cal, today)
        except ValueError as exc:
            provider.warn("previous-trading-day", exc)
    raw, rank_source = provider.rankings(category)
    if category == "全部" and len(raw) < 1000:
        raise ValueError("全部榜单少于1000条，疑似截断；不发布误导性的TOP50")
    funds, meta = rank_funds(raw, top_n, today, previous, max_age)
    overrides = load_overrides(override_path, today)
    for i, f in enumerate(funds, 1):
        print(f"Fund {i}/{len(funds)}: {f['code']}", flush=True)
        f["ranking_source_id"] = rank_source
        f["profile"] = provider.profile(f["code"])
        f["holdings"], f["holdings_info"] = provider.holdings(f["code"], today)
    has_local = any(h["market"] in {"SH", "SZ", "BJ"} for f in funds for h in f["holdings"])
    if has_local:
        concept_index, concept_stats = provider.concepts()
    else:
        concept_index, concept_stats = {}, {"boards_total": 0, "boards_completed": 0, "complete": False,
                                            "reason": "No supported domestic-stock holdings"}
    for f in funds:
        for h in f["holdings"]:
            override = overrides.get(h["security_id"])
            if override:
                source_id = "manual:" + h["security_id"]
                provider.sources[source_id] = {"source": "reviewed-override", "url": override["source_url"], "as_of": override["as_of"]}
                h.update({"industry": override.get("industry"), "concepts": override.get("concepts", []),
                          "market_tags": [], "industry_source_id": source_id, "concept_source_ids": [source_id],
                          "classification_status": "reviewed_override"})
            else:
                h.update(provider.industry(h["market"], h["stock_code"]))
                h.update(concept_index.get(h["security_id"], {"concepts": [], "market_tags": [], "concept_source_ids": []}))
            h["concept_mapping_complete"] = concept_stats["complete"] if not override else True
        f["analysis"] = summarize_holdings(f["holdings"])
    missing_profile = sum(bool(f["profile"]["missing_fields"]) for f in funds)
    missing_holdings = sum(not f["holdings"] for f in funds)
    unmapped_stocks = sum(not h.get("industry") for f in funds for h in f["holdings"])
    incomplete_weights = sum(h.get("weight_pct") is None for f in funds for h in f["holdings"])
    possible_truncation = any(f["holdings_info"].get("possibly_truncated") for f in funds)
    status = "partial / 存在缺失" if missing_profile or missing_holdings or unmapped_stocks or incomplete_weights or possible_truncation or not previous or not concept_stats["complete"] else "ok / 已采集"
    meta.update({"demo": False, "status": status, "collected_at": now.isoformat(), "category": category,
                 "ranking_source_id": rank_source, "concept_coverage": concept_stats,
                 "funds_with_incomplete_profile": missing_profile, "funds_without_holdings": missing_holdings,
                 "unmapped_industry_rows": unmapped_stocks, "missing_weight_rows": incomplete_weights,
                 "possible_holdings_truncation": possible_truncation,
                 "universe_policy": "Eastmoney open-end ranking universe; not money-market yield ranking; share classes separate",
                 "daily_rank_policy": "only NAV date == previous verified mainland trading session, strictly before collection date",
                 "currency_policy": "source-reported values, no FX conversion; NAV level rank is not performance",
                 "method": "Deterministic classification and NAV-weight aggregation. No AI-generated facts."})
    return {"metadata": meta, "funds": funds, "sources": provider.sources, "warnings": provider.warnings}


def main(argv=None):
    parser = argparse.ArgumentParser(description="每日基金近一年TOP50与公开披露持仓观察")
    parser.add_argument("--category", default="全部", choices=["全部", "股票型", "混合型", "债券型", "指数型", "QDII", "FOF"])
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--max-nav-age", type=int, default=14)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cache", type=Path, default=Path(".cache/fund_tracker"))
    parser.add_argument("--raw", type=Path, default=Path("artifacts/fund-tracker"))
    parser.add_argument("--overrides", type=Path)
    parser.add_argument("--demo", action="store_true", help="仅合成测试数据，默认输出到 demo-output")
    args = parser.parse_args(argv)
    if not 1 <= args.top <= 500 or not 1 <= args.max_nav_age <= 366:
        parser.error("--top must be 1..500; --max-nav-age must be 1..366")
    out = args.output or Path("demo-output/funds" if args.demo else "reports/funds")
    if args.demo and "reports" in out.parts:
        parser.error("拒绝把演示数据写入正式 reports 目录")
    stamp = datetime.now(SHANGHAI).date().isoformat()
    provider = Provider(args.cache, args.raw / stamp)
    try:
        if args.demo:
            report = demo_report(args.top)
        else:
            with bounded_http():
                report = collect(provider, args.category, args.top, args.max_nav_age, args.overrides)
        dated = out / stamp
        write_reports(dated, report)
        latest = out / "latest"
        latest.mkdir(parents=True, exist_ok=True)
        for path in dated.iterdir():
            if path.is_file():
                tmp = latest / (path.name + ".tmp")
                shutil.copyfile(path, tmp)
                tmp.replace(latest / path.name)
        print(f"Report: {dated / 'index.html'}; status={report['metadata']['status']}")
        return 0
    except Exception as exc:
        provider.warn("fatal", exc)
        dump(args.raw / stamp / "failure.json", {"status": "failed", "warnings": provider.warnings, "sources": provider.sources})
        print("FAILED: 不更新 latest；失败原因已写入运行附件", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
