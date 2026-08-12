from __future__ import annotations

import pytest

from src.drive_storage import (
    DEFAULT_SCOPE,
    DriveClient,
    DriveConfig,
    datetime_utc_parts,
    drive_quote,
)


class FakeToken:
    def token(self) -> str:
        return "test-token"

    def invalidate(self) -> None:
        return None


class FakeDriveClient(DriveClient):
    def __init__(self, existing=None):
        config = DriveConfig("root", ("CNINFO", "机构调研"), None, DEFAULT_SCOPE)
        super().__init__(config=config, token_source=FakeToken(), session=object())
        self.existing = existing
        self.upload_calls = []

    def ensure_path(self, segments):
        self.segments = tuple(segments)
        return "folder-1"

    def _find_existing(self, parent_id, announcement_id):
        assert parent_id == "folder-1"
        return self.existing

    def _upload_resumable(self, local_path, metadata, file_id, mime_type):
        self.upload_calls.append((local_path, metadata, file_id, mime_type))
        return {"id": file_id or "new-file", "webViewLink": "https://drive.test/file"}


def record(sha="a" * 64):
    return {
        "announcement_id": "1220000001",
        "stock_code": "000001",
        "publish_date": "2026-08-11",
        "format_detected": "pdf",
        "sha256": sha,
    }


def test_drive_quote_escapes_query_values():
    assert drive_quote("a'b\\c") == "a\\'b\\\\c"


def test_config_uses_shared_drive_as_default_root(monkeypatch):
    monkeypatch.setenv("GDRIVE_SHARED_DRIVE_ID", "drive-1")
    monkeypatch.delenv("GDRIVE_ROOT_FOLDER_ID", raising=False)
    config = DriveConfig.from_env()
    assert config.root_folder_id == "drive-1"
    assert config.base_segments == ("CNINFO", "机构调研")


def test_upsert_skips_identical_sha(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF-test")
    existing = {
        "id": "file-1",
        "appProperties": {"cninfo_sha256": "a" * 64, "cninfo_version": "2"},
    }
    client = FakeDriveClient(existing)
    result = client.upsert_record(path, record())
    assert result["status"] == "skipped"
    assert result["version"] == 2
    assert client.upload_calls == []


def test_upsert_updates_changed_announcement(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF-new")
    existing = {
        "id": "file-1",
        "appProperties": {"cninfo_sha256": "b" * 64, "cninfo_version": "2"},
    }
    client = FakeDriveClient(existing)
    result = client.upsert_record(path, record())
    assert result["status"] == "updated"
    assert result["version"] == 3
    assert client.upload_calls[0][2] == "file-1"


def test_upsert_creates_new_file(tmp_path):
    path = tmp_path / "a.docx"
    path.write_bytes(b"PK-test")
    data = record()
    data["format_detected"] = "docx"
    client = FakeDriveClient()
    result = client.upsert_record(path, data)
    assert result["status"] == "created"
    metadata = client.upload_calls[0][1]
    assert metadata["parents"] == ["folder-1"]
    assert metadata["appProperties"]["cninfo_announcement_id"] == "1220000001"


@pytest.mark.parametrize(
    ("run_id", "expected"),
    [
        ("20260812T010203Z", ("2026", "2026-08")),
        ("bad", ("unknown", "unknown")),
    ],
)
def test_datetime_utc_parts(run_id, expected):
    assert datetime_utc_parts(run_id) == expected
