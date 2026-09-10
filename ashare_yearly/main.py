"""命令行入口。

本程序只采集新股：默认股票池为近一年内上市的全部新股。

常用命令：
    python -m ashare_yearly.main                     # 近一年全部新股
    python -m ashare_yearly.main --deep-limit 30     # 只取最近上市的 30 只
    python -m ashare_yearly.main --codes 301999      # 只复跑指定新股（调试）
    python -m ashare_yearly.main --steps index,sentiment
    python -m ashare_yearly.main --self-check
    python -m ashare_yearly.main --offline-demo
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from .config import ALL_STEPS, DEFAULT_CACHE_DIR, DEFAULT_OUT_DIR, Config


def _parse_date(text: str) -> date:
    cleaned = text.strip().replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"日期格式无法识别: {text}（请用 YYYY-MM-DD）")


def _split(text: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in text.replace("，", ",").split(",") if part.strip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ashare_yearly",
        description="采集 A 股近一年新股的详细信息（akshare 优先，东财/同花顺 兜底）并生成 HTML 报告",
    )
    parser.add_argument("--codes", type=_split, default=(), help="只采集这些新股代码（调试用，逗号分隔）")
    parser.add_argument("--deep-limit", type=int, default=0, help="新股数量上限，0=近一年全部（默认）")
    parser.add_argument("--first-days", type=int, default=7, help="新股上市后采集的交易日数，默认 7")
    parser.add_argument("--intraday-days", type=int, default=10, help="只对距今 N 天内的交易日取分时，0=不取，默认 10")
    parser.add_argument("--news-limit", type=int, default=20, help="只为最近上市的前 N 只新股抓个股资讯，默认 20")
    parser.add_argument("--steps", type=_split, default=None, help=f"执行步骤，可选 {','.join(ALL_STEPS)}")
    parser.add_argument("--end", type=_parse_date, default=None, help="区间结束日，默认今天")
    parser.add_argument("--lookback-days", type=int, default=365, help="回看天数，默认 365")
    parser.add_argument("--news-per-stock", type=int, default=8, help="每只股票最多保留的资讯条数")
    parser.add_argument("--adjust", choices=["qfq", "hfq", ""], default="qfq", help="复权方式，默认前复权")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="输出目录")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, help="HTTP 缓存目录")
    parser.add_argument("--min-interval", type=float, default=0.35, help="请求最小间隔秒，防频控")
    parser.add_argument("--timeout", type=float, default=20.0, help="单次请求超时秒")
    parser.add_argument("--retries", type=int, default=3, help="单个接口重试次数")
    parser.add_argument("--no-akshare", action="store_true", help="禁用 akshare，直接走公开接口兜底")
    parser.add_argument("--no-cache", action="store_true", help="禁用本地 HTTP 缓存")
    parser.add_argument("--self-check", action="store_true", help="只探测关键接口可用性，不生成报告")
    parser.add_argument("--offline-demo", action="store_true", help="用内置合成数据渲染一份演示报告（不联网）")
    return parser


def config_from_args(args: argparse.Namespace) -> Config:
    return Config(
        end=args.end or date.today(),
        lookback_days=args.lookback_days,
        codes=tuple(args.codes or ()),
        deep_limit=args.deep_limit,
        first_days=args.first_days,
        intraday_days=args.intraday_days,
        news_limit=args.news_limit,
        steps=tuple(args.steps) if args.steps else tuple(ALL_STEPS),
        out_dir=args.out,
        cache_dir=args.cache_dir,
        use_akshare=not args.no_akshare,
        use_cache=not args.no_cache,
        min_interval=args.min_interval,
        timeout=args.timeout,
        retries=args.retries,
        news_per_stock=args.news_per_stock,
        adjust=args.adjust,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.offline_demo:
        from .demo import render_demo  # noqa: PLC0415 - 演示不影响正常路径

        path = render_demo(args.out)
        print(f"已生成离线演示报告：{path}")
        return 0

    if args.deep_limit < 0:
        print("--deep-limit 不能为负数（0 表示近一年全部新股）", file=sys.stderr)
        return 2

    config = config_from_args(args)

    if args.self_check:
        from .pipeline import self_check  # noqa: PLC0415

        report = self_check(config)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0 if any(p["ok"] for p in report["probes"]) else 1

    from .pipeline import run  # noqa: PLC0415

    result = run(config)
    print(f"区间：{config.start_dash} ~ {config.end_dash}｜近一年新股 {len(result.get('universe') or [])} 只")
    if result.get("report"):
        print(f"总览：{result['report']}")
        print(f"归档：{result['dated_report']}")
        print(f"明细：{len(result.get('stock_pages') or [])} 页（stocks/<代码>.html）")
    print(f"清单：{result['manifest']}")
    failures = [e for e in result["events"] if e["status"] in ("missing", "error")]
    fallbacks = [e for e in result["events"] if e["status"] == "fallback"]
    print(f"事件：共 {len(result['events'])} 条，兜底 {len(fallbacks)} 条，缺失/失败 {len(failures)} 条")
    for event in failures[:10]:
        print(f"  - 缺失 {event['step']}: {event.get('detail')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
