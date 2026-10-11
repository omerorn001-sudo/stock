"""生成 A 股交易日列表 / GitHub Actions 矩阵（用于按日期区间补全报告）。

数据源：腾讯财经上证指数日K（数据中心网络可达），失败时回退东方财富。

  python scripts/trading_days.py --start 2026-01-01 --end 2026-10-09            # 打印交易日
  python scripts/trading_days.py --start 2026-01-01 --end 2026-10-09 --weekly   # 每周最后一个交易日
  python scripts/trading_days.py ... --chunk 10 --matrix                         # 输出 {"include":[{"dates":"d1 d2 ..."}]}
  python scripts/trading_days.py ... --skip-existing 'reports/{date}.html'       # 跳过已存在的报告
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}


def _get(url: str) -> dict:
    last: Exception | None = None
    for attempt in range(4):
        try:
            with urlopen(Request(url, headers=UA), timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"请求失败：{url}：{last}")


def trading_days(start: date, end: date) -> list[str]:
    days: set[str] = set()
    try:
        data = _get(
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
            f"param=sh000001,day,{start.isoformat()},{end.isoformat()},800,"
        )["data"]["sh000001"]
        for row in data.get("day") or data.get("qfqday") or []:
            days.add(str(row[0]))
    except Exception as exc:  # noqa: BLE001
        print(f"腾讯日K失败，改用东方财富：{exc}", file=sys.stderr)
        data = _get(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.000001&klt=101&fqt=1"
            f"&fields1=f1&fields2=f51&beg={start:%Y%m%d}&end={end:%Y%m%d}"
        )
        for line in (data.get("data") or {}).get("klines") or []:
            days.add(line.split(",")[0])
    result = sorted(d for d in days if start.isoformat() <= d <= end.isoformat())
    if not result:
        raise RuntimeError("区间内没有交易日")
    return result


def last_of_week(days: list[str]) -> list[str]:
    weeks: dict[tuple[int, int], str] = {}
    for d in days:
        iso = date.fromisoformat(d).isocalendar()
        weeks[(iso[0], iso[1])] = d  # 已排序，保留每周最后一个
    return sorted(weeks.values())


def main() -> int:
    parser = argparse.ArgumentParser()
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", default="")
    parser.add_argument("--weekly", action="store_true", help="只取每周最后一个交易日")
    parser.add_argument("--skip-existing", default="", help="路径模板，含 {date}；文件已存在则跳过")
    parser.add_argument("--chunk", type=int, default=1, help="每个矩阵任务处理的日期数")
    parser.add_argument("--matrix", action="store_true", help="输出 GitHub Actions 矩阵 JSON")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else today
    if end > today:
        end = today
    if start > end:
        raise SystemExit("开始日期晚于结束日期")
    days = trading_days(start - timedelta(days=0), end)
    if args.weekly:
        days = last_of_week(days)
    if args.skip_existing:
        days = [d for d in days if not Path(args.skip_existing.format(date=d)).exists()]

    if not args.matrix:
        print("\n".join(days))
        return 0
    size = max(1, args.chunk)
    chunks = [days[i:i + size] for i in range(0, len(days), size)]
    if len(chunks) > 256:
        raise SystemExit(f"任务数 {len(chunks)} 超过 256，请增大 --chunk")
    include = [{"label": f"{c[0]}~{c[-1]}", "dates": " ".join(c)} for c in chunks]
    print(json.dumps({"include": include}, ensure_ascii=False))
    print(f"共 {len(days)} 个日期，{len(chunks)} 个任务", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
