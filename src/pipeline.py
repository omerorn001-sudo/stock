"""完整流水线：巨潮发现/下载 -> Google Drive 幂等归档 -> 运行清单。

- 第一部分由 src.phase1 提供；
- 第二部分由 src.drive_storage 提供；
- 第三部分的定时、回看、回填和告警由 GitHub Workflows 调用本入口。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import phase1
from .apps_script_storage import AppsScriptDriveClient, AppsScriptError
from .drive_storage import DriveClient, DriveError

log = logging.getLogger("juchao.pipeline")

DRIVE_FIELDS = (
    "drive_status",
    "drive_file_id",
    "drive_path",
    "drive_version",
    "drive_web_view_link",
    "uploaded_at",
)
DriveStorageClient = DriveClient | AppsScriptDriveClient


def build_drive_client() -> DriveStorageClient:
    """Apps Script 配置存在时优先使用简化后端，否则使用 Drive API。"""
    apps_values = (
        os.getenv("GDRIVE_APPS_SCRIPT_URL", "").strip(),
        os.getenv("GDRIVE_APPS_SCRIPT_TOKEN", "").strip(),
    )
    if any(apps_values):
        return AppsScriptDriveClient()
    return DriveClient()


def default_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def resolve_range(args: argparse.Namespace) -> tuple[date, date]:
    if args.last_days is not None:
        if args.last_days < 1:
            raise SystemExit("--last-days 必须大于 0")
        end = datetime.now(phase1.TZ).date()
        return end - timedelta(days=args.last_days - 1), end
    if args.start is None or args.end is None:
        raise SystemExit("请提供 --start 与 --end，或使用 --last-days")
    return args.start, args.end


def _init_drive_fields(record: dict[str, Any]) -> None:
    defaults = {
        "drive_status": "not_requested",
        "drive_file_id": None,
        "drive_path": None,
        "drive_version": None,
        "drive_web_view_link": None,
        "uploaded_at": None,
    }
    for key, value in defaults.items():
        record.setdefault(key, value)


def _failure(stage: str, exc: Exception, **context: Any) -> dict[str, Any]:
    return {
        "stage": stage,
        **context,
        "error": f"{type(exc).__name__}: {exc}",
        "at": phase1.now_iso(),
    }


def append_pipeline_summary(summary: dict[str, Any]) -> None:
    target = os.getenv("GITHUB_STEP_SUMMARY")
    if not target:
        return
    lines = [
        "",
        "### Google Drive 归档",
        "",
        f"- 后端：`{summary['drive_backend']}`",
        f"- 新建：**{summary['drive_created']}**",
        f"- 更新：**{summary['drive_updated']}**",
        f"- 已存在跳过：**{summary['drive_skipped']}**",
        f"- Drive 失败：**{summary['drive_failed']}**",
        f"- 隔离文件：**{summary['quarantined']}**",
        f"- 运行 ID：`{summary['run_id']}`",
    ]
    with Path(target).open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="巨潮机构调研完整归档流水线")
    parser.add_argument("--start", type=phase1.parse_date, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", type=phase1.parse_date, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--last-days", type=int, help="回看最近 N 个自然日")
    parser.add_argument("--market", choices=("all", "sh", "sz"), default="all")
    parser.add_argument("--download-files", action="store_true")
    parser.add_argument("--upload-drive", action="store_true")
    parser.add_argument("--max-files", type=int, default=0, help="0 表示不限制")
    parser.add_argument("--output", type=Path, default=Path("artifacts/juchao-run"))
    parser.add_argument("--run-id", default=default_run_id())
    parser.add_argument("--fail-on-empty", action="store_true")
    parser.add_argument("--skip-run-manifest-upload", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    start, end = resolve_range(args)
    if start > end:
        raise SystemExit("开始日期不得晚于结束日期")
    if args.max_files < 0:
        raise SystemExit("--max-files 不得为负数")

    download_requested = bool(args.download_files or args.upload_drive)
    session = phase1.make_session()
    records, failures, query_stats = phase1.fetch_range(start, end, args.market, session)
    for record in records:
        _init_drive_fields(record)

    selected = records[: args.max_files] if args.max_files else records
    selected_ids = {record["announcement_id"] for record in selected}
    for record in records:
        if record["announcement_id"] not in selected_ids:
            record["download_status"] = "not_selected"

    if download_requested:
        for record in selected:
            try:
                phase1.download_attachment(record, args.output, session)
            except Exception as exc:  # noqa: BLE001
                if record["download_status"] == "not_requested":
                    record["download_status"] = "failed"
                failures.append(
                    _failure(
                        "download",
                        exc,
                        announcement_id=record["announcement_id"],
                        attachment_url=record["attachment_url"],
                    )
                )

    drive_client: DriveStorageClient | None = None
    drive_backend = "not_requested"
    if args.upload_drive:
        try:
            drive_client = build_drive_client()
            drive_backend = getattr(drive_client, "backend_name", "drive_api")
        except Exception as exc:  # noqa: BLE001
            drive_backend = "configuration_failed"
            failures.append(_failure("drive_config", exc))
            for record in selected:
                if record["download_status"] == "ok":
                    record["drive_status"] = "failed"

    if drive_client is not None:
        for record in selected:
            if record["download_status"] != "ok" or not record.get("local_path"):
                continue
            local_path = args.output / str(record["local_path"])
            try:
                result = drive_client.upsert_record(local_path, record)
                record["drive_status"] = result["status"]
                record["drive_file_id"] = result.get("file_id")
                record["drive_path"] = result.get("drive_path")
                record["drive_version"] = result.get("version")
                record["drive_web_view_link"] = result.get("web_view_link")
                record["uploaded_at"] = phase1.now_iso()
            except Exception as exc:  # noqa: BLE001
                record["drive_status"] = "failed"
                failures.append(
                    _failure(
                        "drive_upload",
                        exc,
                        announcement_id=record["announcement_id"],
                        local_path=str(local_path),
                    )
                )

    format_counts = {name: 0 for name in phase1.SUPPORTED_FORMATS}
    for record in records:
        detected = record.get("format_detected")
        if detected in format_counts:
            format_counts[detected] += 1

    summary = {
        "phase": "complete",
        "run_id": args.run_id,
        "generated_at": phase1.now_iso(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "market": args.market,
        **query_stats,
        "download_requested": download_requested,
        "upload_drive_requested": bool(args.upload_drive),
        "drive_backend": drive_backend,
        "download_limit": args.max_files,
        "downloaded": sum(r["download_status"] == "ok" for r in records),
        "quarantined": sum(r["download_status"] == "quarantined" for r in records),
        "pdf": format_counts["pdf"],
        "doc": format_counts["doc"],
        "docx": format_counts["docx"],
        "drive_created": sum(r["drive_status"] == "created" for r in records),
        "drive_updated": sum(r["drive_status"] == "updated" for r in records),
        "drive_skipped": sum(r["drive_status"] == "skipped" for r in records),
        "drive_failed": sum(r["drive_status"] == "failed" for r in records),
        "failures": len(failures),
    }
    phase1.write_outputs(args.output, records, failures, summary)

    if drive_client is not None and not args.skip_run_manifest_upload:
        manifest_failures = []
        for name in ("manifest.json", "manifest.csv", "failures.json"):
            path = args.output / name
            try:
                drive_client.upload_run_file(path, args.run_id)
            except Exception as exc:  # noqa: BLE001
                manifest_failures.append(_failure("drive_run_manifest", exc, file=name))
        if manifest_failures:
            failures.extend(manifest_failures)
            summary["failures"] = len(failures)
            phase1.write_outputs(args.output, records, failures, summary)

    phase1.write_step_summary(summary)
    append_pipeline_summary(summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.fail_on_empty and not records:
        log.error("结果异常为空，fail-on-empty 已启用")
        return 1
    if failures:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (DriveError, AppsScriptError) as exc:
        log.error("Drive 失败：%s", exc)
        sys.exit(1)
