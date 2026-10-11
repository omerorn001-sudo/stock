"""把历史日期区间拆成 GitHub Actions matrix（自然月内再按 --days 天切分，避免单作业超时）。"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def month_ranges(start: date, end: date, days: int = 0) -> list[dict[str, str]]:
    if start > end:
        raise ValueError("开始日期不得晚于结束日期")
    result: list[dict[str, str]] = []
    current = start
    while current <= end:
        next_month = date(current.year + 1, 1, 1) if current.month == 12 else date(current.year, current.month + 1, 1)
        month_end = min(end, next_month - timedelta(days=1))
        piece = current
        while piece <= month_end:
            piece_end = month_end if days <= 0 else min(month_end, piece + timedelta(days=days - 1))
            result.append({"start": piece.isoformat(), "end": piece_end.isoformat()})
            piece = piece_end + timedelta(days=1)
        current = next_month
    if len(result) > 250:
        raise ValueError("单次回填最多 250 个分段，请拆分任务")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, type=parse_date)
    parser.add_argument("--end", required=True, type=parse_date)
    parser.add_argument("--days", type=int, default=0, help="每段天数；0 表示整月")
    args = parser.parse_args()
    matrix = {"include": month_ranges(args.start, args.end, args.days)}
    compact = json.dumps(matrix, ensure_ascii=False, separators=(",", ":"))
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"matrix={compact}\n")
    print(json.dumps(matrix, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
