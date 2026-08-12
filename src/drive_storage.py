"""Google Drive API 归档：双认证、文件夹创建、公告 ID 去重与修订更新。

支持两种正式认证路径：
1. 个人 My Drive：OAuth client_id/client_secret/refresh_token；
2. Workspace Shared Drive：GitHub OIDC + Workload Identity Federation，
   由 google-github-actions/auth 生成 Application Default Credentials。

本模块不保存凭据，也不把令牌写入日志。
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import requests

DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"
DEFAULT_SCOPE = "https://www.googleapis.com/auth/drive"
TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}
FORMAT_MIME = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "json": "application/json",
    "csv": "text/csv",
}

log = logging.getLogger("juchao.drive")


class DriveError(RuntimeError):
    """Drive 配置、认证或 API 操作失败。"""


class TokenSource(Protocol):
    def token(self) -> str:
        ...

    def invalidate(self) -> None:
        ...


@dataclass(frozen=True)
class DriveConfig:
    root_folder_id: str
    base_segments: tuple[str, ...]
    shared_drive_id: str | None
    scope: str

    @classmethod
    def from_env(cls) -> "DriveConfig":
        root = os.getenv("GDRIVE_ROOT_FOLDER_ID", "root").strip() or "root"
        base = os.getenv("GDRIVE_BASE_PATH", "CNINFO/机构调研")
        segments = tuple(part.strip() for part in base.split("/") if part.strip())
        if not segments:
            raise DriveError("GDRIVE_BASE_PATH 不能为空")
        shared = os.getenv("GDRIVE_SHARED_DRIVE_ID", "").strip() or None
        scope = os.getenv("GDRIVE_SCOPE", DEFAULT_SCOPE).strip() or DEFAULT_SCOPE
        if shared and root == "root":
            root = shared
        return cls(root, segments, shared, scope)


class GoogleTokenSource:
    """延迟加载 google-auth，自动刷新 OAuth 或 WIF/ADC 凭据。"""

    def __init__(self, config: DriveConfig) -> None:
        self.config = config
        self._credentials: Any | None = None
        self._static_token = os.getenv("GDRIVE_ACCESS_TOKEN", "").strip() or None

    def _load(self) -> Any:
        try:
            import google.auth
            from google.oauth2.credentials import Credentials
        except ImportError as exc:
            raise DriveError("缺少 google-auth，请先安装 requirements.txt") from exc

        refresh_token = os.getenv("GDRIVE_OAUTH_REFRESH_TOKEN", "").strip()
        client_id = os.getenv("GDRIVE_OAUTH_CLIENT_ID", "").strip()
        client_secret = os.getenv("GDRIVE_OAUTH_CLIENT_SECRET", "").strip()
        oauth_values = (refresh_token, client_id, client_secret)
        if any(oauth_values):
            if not all(oauth_values):
                raise DriveError("个人 OAuth 需要同时配置 client_id、client_secret、refresh_token")
            return Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=[self.config.scope],
            )

        try:
            credentials, _project = google.auth.default(scopes=[self.config.scope])
        except Exception as exc:  # noqa: BLE001
            raise DriveError(
                "没有可用的 Drive 凭据：请配置个人 OAuth，或在 Actions 中启用 WIF/ADC"
            ) from exc
        return credentials

    def token(self) -> str:
        if self._static_token:
            return self._static_token
        if self._credentials is None:
            self._credentials = self._load()
        if not getattr(self._credentials, "valid", False):
            try:
                from google.auth.transport.requests import Request

                self._credentials.refresh(Request())
            except Exception as exc:  # noqa: BLE001
                raise DriveError("刷新 Google Drive 访问令牌失败") from exc
        token = getattr(self._credentials, "token", None)
        if not token:
            raise DriveError("Google Drive 凭据没有生成访问令牌")
        return str(token)

    def invalidate(self) -> None:
        if self._static_token:
            return
        if self._credentials is not None:
            try:
                self._credentials.expiry = None
                self._credentials.token = None
            except Exception:  # noqa: BLE001
                self._credentials = None


def drive_quote(value: str) -> str:
    """转义 Drive 查询语句中的字符串值。"""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def safe_property(value: Any, limit: int = 120) -> str:
    return str(value if value is not None else "")[:limit]


class DriveClient:
    def __init__(
        self,
        config: DriveConfig | None = None,
        token_source: TokenSource | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.config = config or DriveConfig.from_env()
        self.token_source = token_source or GoogleTokenSource(self.config)
        self.session = session or requests.Session()
        self._folder_cache: dict[tuple[str, str], str] = {}

    def _request(
        self,
        method: str,
        url: str,
        *,
        expected: set[int] | None = None,
        retry_body: bytes | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        expected = expected or {200}
        base_headers = dict(kwargs.pop("headers", {}) or {})
        last_response: requests.Response | None = None
        for attempt in range(1, 6):
            headers = dict(base_headers)
            headers["Authorization"] = f"Bearer {self.token_source.token()}"
            if retry_body is not None:
                kwargs["data"] = retry_body
            response = self.session.request(method, url, headers=headers, timeout=60, **kwargs)
            last_response = response
            if response.status_code in expected:
                return response
            if response.status_code == 401 and attempt == 1:
                self.token_source.invalidate()
                continue
            if response.status_code in TRANSIENT_STATUS and attempt < 5:
                retry_after = response.headers.get("Retry-After", "")
                try:
                    delay = max(float(retry_after), 1.0)
                except ValueError:
                    delay = min(2 ** (attempt - 1), 16)
                time.sleep(delay)
                continue
            detail = response.text[:500].replace("\n", " ")
            raise DriveError(f"Drive API {method} {response.status_code}: {detail}")
        status = last_response.status_code if last_response is not None else "unknown"
        raise DriveError(f"Drive API 重试耗尽，最后状态 {status}")

    def _list(self, query: str, fields: str) -> list[dict[str, Any]]:
        params: dict[str, str] = {
            "q": query,
            "spaces": "drive",
            "pageSize": "100",
            "fields": f"nextPageToken,files({fields})",
            "includeItemsFromAllDrives": "true",
            "supportsAllDrives": "true",
        }
        if self.config.shared_drive_id:
            params.update({"corpora": "drive", "driveId": self.config.shared_drive_id})
        files: list[dict[str, Any]] = []
        while True:
            response = self._request("GET", f"{DRIVE_API}/files", params=params)
            payload = response.json()
            files.extend(payload.get("files") or [])
            token = payload.get("nextPageToken")
            if not token:
                return files
            params["pageToken"] = str(token)

    def ensure_folder(self, parent_id: str, name: str) -> str:
        key = (parent_id, name)
        if key in self._folder_cache:
            return self._folder_cache[key]
        query = (
            f"name='{drive_quote(name)}' and '{drive_quote(parent_id)}' in parents "
            f"and mimeType='{FOLDER_MIME}' and trashed=false"
        )
        existing = self._list(query, "id,name")
        if existing:
            folder_id = str(existing[0]["id"])
            self._folder_cache[key] = folder_id
            return folder_id

        metadata = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
        response = self._request(
            "POST",
            f"{DRIVE_API}/files",
            params={"supportsAllDrives": "true", "fields": "id,name"},
            json=metadata,
            expected={200, 201},
        )
        folder_id = str(response.json()["id"])
        self._folder_cache[key] = folder_id
        return folder_id

    def ensure_path(self, segments: tuple[str, ...] | list[str]) -> str:
        parent = self.config.root_folder_id
        for segment in segments:
            if segment:
                parent = self.ensure_folder(parent, segment)
        return parent

    def _find_existing(self, parent_id: str, announcement_id: str) -> dict[str, Any] | None:
        query = (
            f"'{drive_quote(parent_id)}' in parents and trashed=false and "
            "appProperties has { key='cninfo_announcement_id' and "
            f"value='{drive_quote(announcement_id)}' }}"
        )
        files = self._list(query, "id,name,appProperties,size,modifiedTime,webViewLink")
        return files[0] if files else None

    def _start_resumable(
        self,
        local_path: Path,
        metadata: dict[str, Any],
        file_id: str | None,
        mime_type: str,
    ) -> str:
        if file_id:
            method = "PATCH"
            url = f"{DRIVE_UPLOAD_API}/files/{file_id}"
        else:
            method = "POST"
            url = f"{DRIVE_UPLOAD_API}/files"
        response = self._request(
            method,
            url,
            params={
                "uploadType": "resumable",
                "supportsAllDrives": "true",
                "fields": "id,name,appProperties,size,modifiedTime,webViewLink",
            },
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": mime_type,
                "X-Upload-Content-Length": str(local_path.stat().st_size),
            },
            json=metadata,
            expected={200, 201},
        )
        location = response.headers.get("Location")
        if not location:
            raise DriveError("Drive 未返回 resumable upload URL")
        return location

    def _upload_resumable(
        self,
        local_path: Path,
        metadata: dict[str, Any],
        file_id: str | None,
        mime_type: str,
    ) -> dict[str, Any]:
        location = self._start_resumable(local_path, metadata, file_id, mime_type)
        body = local_path.read_bytes()
        response = self._request(
            "PUT",
            location,
            headers={"Content-Type": mime_type, "Content-Length": str(len(body))},
            expected={200, 201},
            retry_body=body,
        )
        return dict(response.json())

    def upsert_record(self, local_path: Path, record: dict[str, Any]) -> dict[str, Any]:
        publish_date = str(record["publish_date"])
        folder_segments = (
            *self.config.base_segments,
            publish_date[:4],
            publish_date[:7],
            publish_date,
        )
        parent_id = self.ensure_path(folder_segments)
        ann_id = safe_property(record["announcement_id"])
        sha256 = safe_property(record["sha256"])
        existing = self._find_existing(parent_id, ann_id)
        old_sha = ((existing or {}).get("appProperties") or {}).get("cninfo_sha256")
        drive_path = "/".join((*folder_segments, local_path.name))
        if existing and old_sha == sha256:
            return {
                "status": "skipped",
                "file_id": existing["id"],
                "drive_path": drive_path,
                "version": int(((existing.get("appProperties") or {}).get("cninfo_version") or 1)),
                "web_view_link": existing.get("webViewLink"),
            }

        version = int(((existing or {}).get("appProperties") or {}).get("cninfo_version") or 0) + 1
        metadata: dict[str, Any] = {
            "name": local_path.name,
            "appProperties": {
                "cninfo_announcement_id": ann_id,
                "cninfo_sha256": sha256,
                "cninfo_stock_code": safe_property(record.get("stock_code")),
                "cninfo_publish_date": safe_property(publish_date),
                "cninfo_format": safe_property(record.get("format_detected")),
                "cninfo_version": str(version),
            },
        }
        file_id = str(existing["id"]) if existing else None
        if not existing:
            metadata["parents"] = [parent_id]
        extension = str(record.get("format_detected") or local_path.suffix.lstrip(".")).lower()
        mime_type = FORMAT_MIME.get(extension) or mimetypes.guess_type(local_path.name)[0]
        mime_type = mime_type or "application/octet-stream"
        uploaded = self._upload_resumable(local_path, metadata, file_id, mime_type)
        return {
            "status": "updated" if existing else "created",
            "file_id": uploaded.get("id") or file_id,
            "drive_path": drive_path,
            "version": version,
            "web_view_link": uploaded.get("webViewLink"),
        }

    def upload_run_file(self, local_path: Path, run_id: str) -> dict[str, Any]:
        now = datetime_utc_parts(run_id)
        segments = (*self.config.base_segments, "_runs", now[0], now[1], run_id)
        parent_id = self.ensure_path(segments)
        synthetic_id = f"run:{run_id}:{local_path.name}"
        existing = self._find_existing(parent_id, synthetic_id)
        sha256 = file_sha256(local_path)
        old_sha = ((existing or {}).get("appProperties") or {}).get("cninfo_sha256")
        if existing and old_sha == sha256:
            return {"status": "skipped", "file_id": existing["id"]}
        metadata: dict[str, Any] = {
            "name": local_path.name,
            "appProperties": {
                "cninfo_announcement_id": synthetic_id,
                "cninfo_sha256": sha256,
                "cninfo_run_id": safe_property(run_id),
                "cninfo_version": "1",
            },
        }
        file_id = str(existing["id"]) if existing else None
        if not existing:
            metadata["parents"] = [parent_id]
        extension = local_path.suffix.lower().lstrip(".")
        mime_type = FORMAT_MIME.get(extension) or "application/octet-stream"
        uploaded = self._upload_resumable(local_path, metadata, file_id, mime_type)
        return {
            "status": "updated" if existing else "created",
            "file_id": uploaded.get("id") or file_id,
        }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def datetime_utc_parts(run_id: str) -> tuple[str, str]:
    """从 YYYYMMDDTHHMMSSZ 运行 ID 提取年和年月。"""
    if len(run_id) >= 6 and run_id[:6].isdigit():
        return run_id[:4], f"{run_id[:4]}-{run_id[4:6]}"
    return "unknown", "unknown"
