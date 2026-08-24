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


def test_dataset_operation(tmp_path):
    path = tmp_path / "龙虎榜.csv"
    path.write_text("证券代码\n000001\n", encoding="utf-8")
    session = FakeSession(
        [
            FakeResponse(
                {
                    "ok": True,
                    "result": {
                        "status": "created",
                        "file_id": "1",
                        "drive_path": "CNINFO/龙虎榜/龙虎榜.csv",
                    },
                }
            )
        ]
    )
    result = AppsScriptDriveClient(
        config=config(), session=session
    ).upload_dataset_file(
        path,
        dataset="dragon_tiger",
        data_date="2026-08-11",
    )
    assert result["status"] == "created"
    sent = session.calls[0][1]["json"]
    assert sent["operation"] == "dataset_file"
    assert sent["dataset"] == "dragon_tiger"
    assert base64.b64decode(sent["content_base64"]).startswith(
        "证券代码".encode()
    )


def test_dataset_validation(tmp_path):
    path = tmp_path / "data.json"
    path.write_text("{}")
    with pytest.raises(AppsScriptError):
        AppsScriptDriveClient(
            config=config(), session=FakeSession([])
        ).upload_dataset_file(
            path,
            dataset="龙虎榜",
            data_date="2026-08-11",
        )


def test_default_max_bytes(monkeypatch):
    monkeypatch.setenv(
        "GDRIVE_APPS_SCRIPT_URL",
        "https://script.google.com/macros/s/test/exec",
    )
    monkeypatch.setenv("GDRIVE_APPS_SCRIPT_TOKEN", "x" * 32)
    monkeypatch.setenv("GDRIVE_APPS_SCRIPT_MAX_BYTES", "")
    assert AppsScriptConfig.from_env().max_bytes == DEFAULT_MAX_BYTES


def test_retries_apps_script_lock_contention(tmp_path, monkeypatch):
    path = tmp_path / "龙虎榜.csv"
    path.write_text("证券代码\n000001\n", encoding="utf-8")
    session = FakeSession(
        [
            FakeResponse(
                {
                    "ok": False,
                    "code": "request_failed",
                    "error": "另一个上传任务正在运行，请稍后重试",
                }
            ),
            FakeResponse(
                {
                    "ok": True,
                    "result": {
                        "status": "created",
                        "file_id": "1",
                        "drive_path": "CNINFO/龙虎榜/龙虎榜.csv",
                    },
                }
            ),
        ]
    )
    monkeypatch.setattr("src.apps_script_storage.time.sleep", lambda _: None)

    result = AppsScriptDriveClient(
        config=config(), session=session
    ).upload_dataset_file(
        path,
        dataset="dragon_tiger",
        data_date="2026-08-11",
    )

    assert result["status"] == "created"
    assert len(session.calls) == 2


def test_does_not_retry_non_transient_apps_script_error(tmp_path, monkeypatch):
    path = tmp_path / "龙虎榜.csv"
    path.write_text("证券代码\n000001\n", encoding="utf-8")
    session = FakeSession(
        [
            FakeResponse(
                {
                    "ok": False,
                    "code": "request_failed",
                    "error": "上传令牌无效",
                }
            )
        ]
    )
    monkeypatch.setattr("src.apps_script_storage.time.sleep", lambda _: None)

    with pytest.raises(AppsScriptError, match="上传令牌无效"):
        AppsScriptDriveClient(
            config=config(), session=session
        ).upload_dataset_file(
            path,
            dataset="dragon_tiger",
            data_date="2026-08-11",
        )

    assert len(session.calls) == 1
