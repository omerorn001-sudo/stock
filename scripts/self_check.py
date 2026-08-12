"""不访问网络的完整流水线自检。"""

from __future__ import annotations

import argparse
import json
import tempfile
import zipfile
from datetime import date
from pathlib import Path

from scripts.backfill_matrix import month_ranges
from src import phase1, pipeline
from src.drive_storage import DEFAULT_SCOPE, DriveClient, DriveConfig
from src.pipeline import resolve_range


class FakeToken:
    def token(self) -> str:
        return "offline-test-token"

    def invalidate(self) -> None:
        return None


class FakeDriveClient(DriveClient):
    def __init__(self, existing=None) -> None:
        config = DriveConfig("root", ("CNINFO", "机构调研"), None, DEFAULT_SCOPE)
        super().__init__(config=config, token_source=FakeToken(), session=object())
        self.existing = existing
        self.uploads: list[tuple[dict, str | None, str]] = []

    def ensure_path(self, segments):
        self.segments = tuple(segments)
        return "folder-test"

    def _find_existing(self, parent_id, announcement_id):
        assert parent_id == "folder-test"
        return self.existing

    def _upload_resumable(self, local_path, metadata, file_id, mime_type):
        self.uploads.append((metadata, file_id, mime_type))
        return {"id": file_id or "created-file", "webViewLink": "https://drive.test/file"}


def sample_record(sha256: str = "a" * 64) -> dict:
    return {
        "announcement_id": "1220000001",
        "stock_code": "000001",
        "publish_date": "2026-08-11",
        "format_detected": "pdf",
        "sha256": sha256,
    }


def run_checks() -> dict[str, str]:
    assert phase1.market_from_code("600519") == "sh"
    assert phase1.market_from_code("000001") == "sz"
    assert phase1.market_from_code("920008") == "bj"
    assert phase1.resolve_attachment_url("https://example.com/a.pdf") is None

    raw = {
        "announcementId": "1220000001",
        "secCode": "000001",
        "secName": "示例股份",
        "announcementTitle": "投资者关系活动记录表",
        "announcementTime": 1741881600000,
        "adjunctUrl": "finalpage/2025-03-14/1220000001.DOCX",
    }
    normalized, reason = phase1.normalize_announcement(raw, "机构调研")
    assert reason is None and normalized is not None
    assert normalized["format_hint"] == "docx"

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        pdf = root / "a.bin"
        pdf.write_bytes(b"%PDF-1.7\nself-check")
        assert phase1.sniff_format(pdf) == "pdf"

        doc = root / "b.bin"
        doc.write_bytes(phase1.OLE_MAGIC + b"self-check")
        assert phase1.sniff_format(doc) == "doc"

        docx = root / "c.bin"
        with zipfile.ZipFile(docx, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "<document/>")
        assert phase1.sniff_format(docx) == "docx"

        existing = {
            "id": "drive-file",
            "appProperties": {"cninfo_sha256": "a" * 64, "cninfo_version": "2"},
        }
        skipped = FakeDriveClient(existing).upsert_record(pdf, sample_record())
        assert skipped["status"] == "skipped" and skipped["version"] == 2

        changed = {
            "id": "drive-file",
            "appProperties": {"cninfo_sha256": "b" * 64, "cninfo_version": "2"},
        }
        updated = FakeDriveClient(changed).upsert_record(pdf, sample_record())
        assert updated["status"] == "updated" and updated["version"] == 3

        created = FakeDriveClient().upsert_record(pdf, sample_record())
        assert created["status"] == "created" and created["file_id"] == "created-file"

        original_fetch = phase1.fetch_range
        original_session = phase1.make_session
        original_download = phase1.download_attachment

        def fake_fetch(_start, _end, _market, _session):
            record = dict(normalized)
            stats = {
                "raw": 1,
                "accepted": 1,
                "excluded_market": 0,
                "excluded_market_filter": 0,
                "excluded_title": 0,
                "excluded_metadata": 0,
                "duplicates": 0,
            }
            return [record], [], stats

        def fake_download(record, output_dir, _session):
            target = output_dir / "files" / "2025" / "2025-03" / "sample.docx"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"PK-offline-pipeline")
            record.update(
                {
                    "format_detected": "docx",
                    "mime_detected": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "size_bytes": target.stat().st_size,
                    "sha256": "c" * 64,
                    "local_path": str(target.relative_to(output_dir)),
                    "download_status": "ok",
                }
            )
            return target

        try:
            phase1.fetch_range = fake_fetch
            phase1.make_session = lambda: object()
            phase1.download_attachment = fake_download
            pipeline_output = root / "pipeline-output"
            exit_code = pipeline.main(
                [
                    "--start",
                    "2025-03-14",
                    "--end",
                    "2025-03-14",
                    "--market",
                    "all",
                    "--download-files",
                    "--output",
                    str(pipeline_output),
                ]
            )
            assert exit_code == 0
            assert (pipeline_output / "manifest.json").exists()
            assert (pipeline_output / "manifest.csv").exists()
            assert (pipeline_output / "failures.json").exists()
        finally:
            phase1.fetch_range = original_fetch
            phase1.make_session = original_session
            phase1.download_attachment = original_download

    ranges = month_ranges(date(2025, 12, 20), date(2026, 2, 3))
    assert ranges == [
        {"start": "2025-12-20", "end": "2025-12-31"},
        {"start": "2026-01-01", "end": "2026-01-31"},
        {"start": "2026-02-01", "end": "2026-02-03"},
    ]

    args = argparse.Namespace(last_days=None, start=date(2026, 8, 1), end=date(2026, 8, 7))
    assert resolve_range(args) == (date(2026, 8, 1), date(2026, 8, 7))

    return {
        "market_filter": "pass",
        "format_detection": "pass",
        "drive_idempotency": "pass",
        "pipeline_orchestration": "pass",
        "backfill_ranges": "pass",
        "pipeline_range": "pass",
    }


def main() -> int:
    print(json.dumps(run_checks(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
