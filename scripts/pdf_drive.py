"""Generate PDF deliverables and archive them through the existing Drive gateway."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.apps_script_storage import AppsScriptDriveClient  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_pdf(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 5:
        return False
    with path.open("rb") as handle:
        return handle.read(5) == b"%PDF-"


def _parse_date(value: str) -> str:
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError
    return value


def _find_executable(env_name: str, candidates: tuple[str, ...]) -> str:
    configured = os.getenv(env_name, "").strip()
    if configured:
        resolved = shutil.which(configured) or configured
        if Path(resolved).is_file() or shutil.which(resolved):
            return resolved
        raise RuntimeError(f"{env_name} 指定的程序不存在：{configured}")
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise RuntimeError("未找到所需程序：" + "、".join(candidates))


def _run(command: list[str], label: str) -> None:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()[-2000:]
        raise RuntimeError(f"{label}失败（退出码 {result.returncode}）：{detail}")


def render_html_pdf(html_path: Path, pdf_path: Path) -> Path:
    html_path = html_path.resolve()
    pdf_path = pdf_path.resolve()
    if not html_path.is_file():
        raise FileNotFoundError(f"HTML 报告不存在：{html_path}")
    browser = _find_executable(
        "CHROME_BIN",
        ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser"),
    )
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.unlink(missing_ok=True)
    _run(
        [
            browser,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--allow-file-access-from-files",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=10000",
            "--no-pdf-header-footer",
            f"--print-to-pdf={pdf_path}",
            html_path.as_uri(),
        ],
        "HTML 转 PDF",
    )
    if not _valid_pdf(pdf_path):
        raise RuntimeError(f"浏览器未生成有效 PDF：{pdf_path}")
    return pdf_path


def _convert_office_pdf(source: Path, target: Path) -> Path:
    office = _find_executable("LIBREOFFICE_BIN", ("libreoffice", "soffice"))
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pdf-convert-") as temp_value:
        temp = Path(temp_value)
        profile = temp / "profile"
        output = temp / "output"
        output.mkdir()
        _run(
            [
                office,
                f"-env:UserInstallation={profile.resolve().as_uri()}",
                "--headless",
                "--convert-to",
                "pdf:writer_pdf_Export",
                "--outdir",
                str(output),
                str(source.resolve()),
            ],
            f"{source.name} 转 PDF",
        )
        generated = output / f"{source.stem}.pdf"
        if not _valid_pdf(generated):
            raise RuntimeError(f"LibreOffice 未生成有效 PDF：{source.name}")
        shutil.move(str(generated), str(target))
    if not _valid_pdf(target):
        raise RuntimeError(f"PDF 输出校验失败：{target}")
    return target


def _record_pdf(output_dir: Path, record: dict[str, Any]) -> tuple[Path, str]:
    relative = str(record.get("local_path") or "")
    source = output_dir / relative
    if not relative or not source.is_file():
        raise FileNotFoundError(f"下载文件不存在：{relative or '(empty)'}")
    source_format = str(record.get("format_detected") or source.suffix.lstrip(".")).lower()
    if source_format == "pdf":
        if not _valid_pdf(source):
            raise RuntimeError(f"原始 PDF 校验失败：{source.name}")
        return source, "already_pdf"
    if source_format not in {"doc", "docx"}:
        raise RuntimeError(f"不支持转换为 PDF 的格式：{source_format or 'unknown'}")
    publish_date = str(record.get("publish_date") or "unknown")
    year = publish_date[:4] if len(publish_date) >= 4 else "unknown"
    month = publish_date[:7] if len(publish_date) >= 7 else "unknown"
    target = output_dir / "pdf" / year / month / f"{source.stem}.pdf"
    return _convert_office_pdf(source, target), "converted"


def _write_manifest_files(
    output_dir: Path,
    document: dict[str, Any],
    failures: list[dict[str, Any]],
) -> None:
    records = list(document.get("records") or [])
    (output_dir / "manifest.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    fieldnames: list[str] = []
    for record in records:
        if isinstance(record, dict):
            for key in record:
                if key not in fieldnames:
                    fieldnames.append(key)
    with (output_dir / "manifest.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        if fieldnames:
            writer.writeheader()
            writer.writerows(record for record in records if isinstance(record, dict))
    (output_dir / "failures.json").write_text(
        json.dumps(failures, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def process_backfill(output_dir: Path, upload_drive: bool) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    manifest_path = output_dir / "manifest.json"
    failures_path = output_dir / "failures.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"回填 manifest 不存在：{manifest_path}")
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = document.get("records") or []
    if not isinstance(records, list):
        raise RuntimeError("manifest.records 不是数组")
    failures = (
        json.loads(failures_path.read_text(encoding="utf-8"))
        if failures_path.is_file()
        else []
    )
    if not isinstance(failures, list):
        raise RuntimeError("failures.json 不是数组")
    initial_failure_count = len(failures)
    client = AppsScriptDriveClient() if upload_drive else None
    if client is not None:
        client.ping()

    ready = converted = conversion_failed = 0
    drive_counts = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
    for raw_record in records:
        if not isinstance(raw_record, dict) or raw_record.get("download_status") != "ok":
            continue
        record = raw_record
        source_format = str(record.get("format_detected") or "")
        source_local_path = str(record.get("local_path") or "")
        try:
            pdf_path, pdf_status = _record_pdf(output_dir, record)
        except Exception as exc:  # noqa: BLE001
            conversion_failed += 1
            record["pdf_status"] = "failed"
            failures.append(
                {
                    "stage": "pdf_export",
                    "announcement_id": record.get("announcement_id"),
                    "local_path": source_local_path,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        ready += 1
        converted += int(pdf_status == "converted")
        pdf_relative = str(pdf_path.relative_to(output_dir))
        pdf_sha = _sha256(pdf_path)
        record.update(
            {
                "source_format_detected": source_format,
                "source_local_path": source_local_path,
                "pdf_status": pdf_status,
                "pdf_local_path": pdf_relative,
                "pdf_size_bytes": pdf_path.stat().st_size,
                "pdf_sha256": pdf_sha,
            }
        )
        if client is None:
            continue
        try:
            upload_record = {
                **record,
                "format_detected": "pdf",
                "mime_detected": "application/pdf",
                "size_bytes": pdf_path.stat().st_size,
                "sha256": pdf_sha,
                "local_path": pdf_relative,
            }
            upload = client.upsert_record(pdf_path, upload_record)
            status = str(upload["status"])
            drive_counts[status] += 1
            record.update(
                {
                    "drive_status": status,
                    "drive_file_id": upload.get("file_id"),
                    "drive_path": upload.get("drive_path"),
                    "drive_version": upload.get("version"),
                    "drive_web_view_link": upload.get("web_view_link"),
                }
            )
        except Exception as exc:  # noqa: BLE001
            record["drive_status"] = "failed"
            drive_counts["failed"] += 1
            failures.append(
                {
                    "stage": "drive_pdf_upload",
                    "announcement_id": record.get("announcement_id"),
                    "local_path": pdf_relative,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    summary = document.setdefault("summary", {})
    if not isinstance(summary, dict):
        raise RuntimeError("manifest.summary 不是对象")
    summary.update(
        {
            "pdf_export_requested": True,
            "pdf_ready": ready,
            "pdf_converted": converted,
            "pdf_failed": conversion_failed,
            "upload_drive_requested": upload_drive,
            "drive_backend": "apps_script" if upload_drive else "not_requested",
            "drive_created": drive_counts["created"],
            "drive_updated": drive_counts["updated"],
            "drive_skipped": drive_counts["skipped"],
            "drive_failed": drive_counts["failed"],
            "failures": len(failures),
        }
    )
    _write_manifest_files(output_dir, document, failures)

    if client is not None:
        run_id = str(summary.get("run_id") or "pdf-backfill")
        for name in ("manifest.json", "manifest.csv", "failures.json"):
            try:
                client.upload_run_file(output_dir / name, run_id)
            except Exception as exc:  # noqa: BLE001
                failures.append(
                    {
                        "stage": "drive_run_manifest",
                        "file": name,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        summary["failures"] = len(failures)
        _write_manifest_files(output_dir, document, failures)

    result = {
        "pdf_ready": ready,
        "pdf_converted": converted,
        "pdf_failed": conversion_failed,
        "drive_created": drive_counts["created"],
        "drive_updated": drive_counts["updated"],
        "drive_skipped": drive_counts["skipped"],
        "drive_failed": drive_counts["failed"],
        "new_failures": len(failures) - initial_failure_count,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    summary_target = os.getenv("GITHUB_STEP_SUMMARY", "").strip()
    if summary_target:
        with Path(summary_target).open("a", encoding="utf-8") as handle:
            handle.write(
                "\n### PDF 与 Drive\n\n"
                f"- PDF 就绪：**{ready}**\n"
                f"- DOC/DOCX 转换：**{converted}**\n"
                f"- PDF 失败：**{conversion_failed}**\n"
                f"- Drive 新建/更新/跳过/失败：**{drive_counts['created']} / "
                f"{drive_counts['updated']} / {drive_counts['skipped']} / "
                f"{drive_counts['failed']}**\n"
            )
    return result


def run_report(args: argparse.Namespace) -> int:
    report_date = _parse_date(args.date)
    pdf_path = render_html_pdf(args.html, args.pdf)
    result: dict[str, Any] = {
        "date": report_date,
        "pdf": str(pdf_path),
        "size_bytes": pdf_path.stat().st_size,
        "sha256": _sha256(pdf_path),
        "drive_status": "not_requested",
        "drive_path": None,
    }
    if args.upload_drive:
        client = AppsScriptDriveClient()
        client.ping()
        upload = client.upload_run_file(
            pdf_path, f"{report_date.replace('-', '')}-stock-report"
        )
        result["drive_status"] = upload["status"]
        result["drive_path"] = upload.get("drive_path")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成 PDF 并上传到 Google Drive")
    subparsers = parser.add_subparsers(dest="command", required=True)

    report = subparsers.add_parser("report", help="把 HTML 报告转为 PDF")
    report.add_argument("--html", type=Path, required=True)
    report.add_argument("--pdf", type=Path, required=True)
    report.add_argument("--date", required=True)
    report.add_argument("--upload-drive", action="store_true")

    backfill = subparsers.add_parser("backfill", help="把回填附件统一转为 PDF")
    backfill.add_argument("--output", type=Path, required=True)
    backfill.add_argument("--upload-drive", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "report":
        return run_report(args)
    result = process_backfill(args.output, args.upload_drive)
    return 1 if result["new_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
