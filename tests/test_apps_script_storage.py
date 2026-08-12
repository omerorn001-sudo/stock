from __future__ import annotations

import base64

import pytest

from src.apps_script_storage import (
    DEFAULT_MAX_BYTES,
    AppsScriptConfig,
    AppsScriptDriveClient,
    AppsScriptError,
)


class FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self.payload = payload
        self.status_code = status_code
        self.text = text
        self.headers = {}

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def config(max_bytes=1024):
    return AppsScriptConfig(
        url="https://script.google.com/macros/s/test-deployment/exec",
        token="t" * 32,
        max_bytes=max_bytes,
        timeout=3,
    )


def record():
    return {
        "announcement_id": "1220000001",
        "stock_code": "000001",
        "publish_date": "2026-08-12",
        "format_detected": "pdf",
        "sha256": "a" * 64,
    }


def test_config_requires_google_https_url(monkeypatch):
    monkeypatch.setenv("GDRIVE_APPS_SCRIPT_URL", "https://example.com/upload")
    monkeypatch.setenv("GDRIVE_APPS_SCRIPT_TOKEN", "x" * 32)
    with pytest.raises(AppsScriptError, match="Google Apps Script"):
        AppsScriptConfig.from_env()


def test_empty_optional_max_bytes_uses_default(monkeypatch):
    monkeypatch.setenv(
        "GDRIVE_APPS_SCRIPT_URL",
        "https://script.google.com/macros/s/test-deployment/exec",
    )
    monkeypatch.setenv("GDRIVE_APPS_SCRIPT_TOKEN", "x" * 32)
    monkeypatch.setenv("GDRIVE_APPS_SCRIPT_MAX_BYTES", "")
    assert AppsScriptConfig.from_env().max_bytes == DEFAULT_MAX_BYTES


def test_upsert_sends_binary_and_maps_result(tmp_path):
    path = tmp_path / "sample.pdf"
    path.write_bytes(b"%PDF-test")
    response = FakeResponse(
        {
            "ok": True,
            "result": {
                "status": "created",
                "file_id": "file-1",
                "drive_path": "CNINFO/机构调研/2026/2026-08/2026-08-12/sample.pdf",
                "version": 1,
                "web_view_link": "https://drive.google.com/file/d/file-1/view",
            },
        }
    )
    session = FakeSession([response])
    client = AppsScriptDriveClient(config=config(), session=session)

    result = client.upsert_record(path, record())

    assert result["status"] == "created"
    assert result["file_id"] == "file-1"
    sent = session.calls[0][1]["json"]
    assert sent["operation"] == "upsert"
    assert sent["token"] == "t" * 32
    assert base64.b64decode(sent["content_base64"]) == b"%PDF-test"
    assert sent["mime_type"] == "application/pdf"


def test_upload_run_file_uses_run_operation(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("{}", encoding="utf-8")
    session = FakeSession(
        [FakeResponse({"ok": True, "result": {"status": "skipped", "file_id": "run-1"}})]
    )
    client = AppsScriptDriveClient(config=config(), session=session)

    result = client.upload_run_file(path, "20260812T010203Z")

    assert result["status"] == "skipped"
    assert session.calls[0][1]["json"]["operation"] == "run_file"


def test_server_error_is_exposed_without_secret(tmp_path):
    path = tmp_path / "sample.pdf"
    path.write_bytes(b"%PDF-test")
    session = FakeSession(
        [FakeResponse({"ok": False, "code": "request_failed", "error": "上传令牌无效"})]
    )
    client = AppsScriptDriveClient(config=config(), session=session)

    with pytest.raises(AppsScriptError, match="上传令牌无效"):
        client.upsert_record(path, record())


def test_oversized_file_is_rejected_before_network(tmp_path):
    path = tmp_path / "large.pdf"
    path.write_bytes(b"12345")
    session = FakeSession([])
    client = AppsScriptDriveClient(config=config(max_bytes=4), session=session)

    with pytest.raises(AppsScriptError, match="超过 Apps Script"):
        client.upsert_record(path, record())
    assert session.calls == []
