"""Resolve a closed-session report date without drifting when Actions starts late."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo("Asia/Shanghai")
SCHEDULE_HOUR_UTC = 11
SCHEDULE_MINUTE_UTC = 30


def resolve_report_date(
    event_name: str,
    *,
    requested_date: str = "",
    run_created_at: str | None = None,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("当前时间必须包含时区")
    beijing_now = now.astimezone(BEIJING)
    if requested_date:
        parsed = datetime.strptime(requested_date, "%Y-%m-%d").date()
        if parsed.isoformat() != requested_date:
            raise ValueError("日期必须使用 YYYY-MM-DD 格式")
        if parsed > beijing_now.date():
            raise ValueError("不能指定未来日期")
        return parsed.isoformat()

    if event_name == "schedule":
        if not run_created_at:
            raise ValueError("定时任务缺少创建时间，拒绝猜测报告日期")
        created = datetime.fromisoformat(run_created_at.replace("Z", "+00:00"))
        if created.tzinfo is None:
            raise ValueError("任务创建时间必须包含时区")
        created = created.astimezone(timezone.utc)
        # Use the last scheduled 11:30 UTC slot preceding run creation. A delayed
        # run created at 16:59 UTC still belongs to that day's 19:30 Beijing slot.
        # Anchor to creation, not job start: the Drive concurrency queue may span days.
        slot = created.replace(
            hour=SCHEDULE_HOUR_UTC, minute=SCHEDULE_MINUTE_UTC, second=0, microsecond=0,
        )
        if slot > created:
            slot -= timedelta(days=1)
        return slot.astimezone(BEIJING).date().isoformat()

    # Manual runs without a date target the latest calendar day whose market
    # close has passed. The report generator still checks holidays/weekends.
    if beijing_now.hour < 15:
        beijing_now -= timedelta(days=1)
    return beijing_now.date().isoformat()


def workflow_created_at() -> str:
    repository = os.environ["GITHUB_REPOSITORY"]
    run_id = os.environ["GITHUB_RUN_ID"]
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    request = Request(
        f"{api}/repos/{repository}/actions/runs/{run_id}",
        headers={
            "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)["created_at"]


def main() -> int:
    parser = argparse.ArgumentParser(description="确定指定日期报告的目标日期")
    parser.add_argument("--date", default="")
    args = parser.parse_args()
    event_name = os.environ.get("GITHUB_EVENT_NAME", "workflow_dispatch")
    try:
        created_at = workflow_created_at() if event_name == "schedule" and not args.date else None
        report_date = resolve_report_date(
            event_name, requested_date=args.date, run_created_at=created_at,
        )
    except Exception as exc:
        print(f"无法确定报告日期：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(report_date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
