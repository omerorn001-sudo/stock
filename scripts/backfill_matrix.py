"""把历史日期区间拆成自然月 GitHub Actions matrix。"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def month_ranges(start: date, end: date) -> list[dict[str, str]]:
    if start > end:
        raise ValueError("开始日期不得晚于结束日期")
    result: list[dict[str, str]] = []
    current = start
    while current <= end:
        next_month = date(current.year + 1, 1, 1) if current.month == 12 else date(current.year, current.month + 1, 1)
        range_end = min(end, next_month - timedelta(days=1))
        result.append({"start": current.isoformat(), "end": range_end.isoformat()})
        current = next_month
    if len(result) > 120:
        raise ValueError("单次回填最多 120 个月，请拆分任务")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, type=parse_date)
    parser.add_argument("--end", required=True, type=parse_date)
    args = parser.parse_args()
    matrix = {"include": month_ranges(args.start, args.end)}
    compact = json.dumps(matrix, ensure_ascii=False, separators=(",", ":"))
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"matrix={compact}\n")
    print(json.dumps(matrix, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
