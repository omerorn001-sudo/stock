"""通过 Google Apps Script Web App 归档到个人 Google Drive。

这是个人 My Drive 的简化后端：用户只需部署一次 Apps Script，并在
GitHub Secrets 中保存 Web App URL 和上传令牌。无需 OAuth Client、
Client Secret、Refresh Token 或 Google Cloud 项目配置。
"""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

ALLOWED_HOSTS = frozenset({"script.google.com", "script.googleusercontent.com"})
TRANSIENT_STATUS = frozenset({408, 429, 500, 502, 503, 504})
DEFAULT_MAX_BYTES = 35 * 1024 * 1024
DEFAULT_TIMEOUT = 120
FORMAT_MIME = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "json": "application/json",
    "csv": "text/csv",
}


class AppsScriptError(RuntimeError):
    """Apps Script 配置、请求或 Drive 操作失败。"""


@dataclass(frozen=True)
class AppsScriptConfig:
    url: str
    token: str
    max_bytes: int = DEFAULT_MAX_BYTES
    timeout: int = DEFAULT_TIMEOUT

    @classmethod
    def from_env(cls) -> "AppsScriptConfig":
        url = os.getenv("GDRIVE_APPS_SCRIPT_URL", "").strip()
        token = os.getenv("GDRIVE_APPS_SCRIPT_TOKEN", "").strip()
        if not url or not token:
            raise AppsScriptError(
                "Apps Script 上传需要同时配置 GDRIVE_APPS_SCRIPT_URL 和 "
                "GDRIVE_APPS_SCRIPT_TOKEN"
            )
        parsed = urlparse(url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
            raise AppsScriptError("GDRIVE_APPS_SCRIPT_URL 必须是 Google Apps Script HTTPS 地址")
        if len(token) < 16:
            raise AppsScriptError("GDRIVE_APPS_SCRIPT_TOKEN 长度至少为 16 个字符")
        raw_max_bytes = os.getenv("GDRIVE_APPS_SCRIPT_MAX_BYTES", "").strip()
        raw_max_bytes = raw_max_bytes or str(DEFAULT_MAX_BYTES)
        try:
            max_bytes = int(raw_max_bytes)
        except ValueError as exc:
            raise AppsScriptError("GDRIVE_APPS_SCRIPT_MAX_BYTES 必须是整数") from exc
        if max_bytes < 1:
            raise AppsScriptError("GDRIVE_APPS_SCRIPT_MAX_BYTES 必须大于 0")
        return cls(url=url, token=token, max_bytes=max_bytes)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def mime_for(path: Path, extension: str | None = None) -> str:
    key = (extension or path.suffix.lstrip(".")).lower()
    return FORMAT_MIME.get(key) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


class AppsScriptDriveClient:
    """与 DriveClient 具有相同 upsert/upload_run_file 接口的简化客户端。"""

    backend_name = "apps_script"

    def __init__(
        self,
        config: AppsScriptConfig | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.config = config or AppsScriptConfig.from_env()
        self.session = session or requests.Session()

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = {"token": self.config.token, **payload}
        last_error: Exception | None = None
        for attempt in range(1, 6):
            try:
                response = self.session.post(
                    self.config.url,
                    json=body,
                    timeout=self.config.timeout,
                    allow_redirects=True,
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt < 5:
                    time.sleep(min(2 ** (attempt - 1), 16))
                    continue
                raise AppsScriptError("无法连接 Apps Script Web App") from exc

            if response.status_code in TRANSIENT_STATUS and attempt < 5:
                retry_after = response.headers.get("Retry-After", "")
                try:
                    delay = max(float(retry_after), 1.0)
                except ValueError:
                    delay = min(2 ** (attempt - 1), 16)
                time.sleep(delay)
                continue
            if response.status_code >= 400:
                detail = response.text[:500].replace("\n", " ")
                raise AppsScriptError(f"Apps Script HTTP {response.status_code}: {detail}")
            try:
                data = response.json()
            except ValueError as exc:
                detail = response.text[:500].replace("\n", " ")
                raise AppsScriptError(
                    "Apps Script 返回非 JSON；请确认使用 /exec 部署地址且访问权限为 Anyone。"
                    f" 响应：{detail}"
                ) from exc
            if not isinstance(data, dict):
                raise AppsScriptError("Apps Script 响应结构异常")
            if not data.get("ok"):
                code = data.get("code", "apps_script_error")
                message = data.get("error", "未知错误")
                raise AppsScriptError(f"Apps Script {code}: {message}")
            result = data.get("result")
            if not isinstance(result, dict):
                raise AppsScriptError("Apps Script 成功响应缺少 result")
            return result
        raise AppsScriptError(f"Apps Script 重试耗尽：{last_error}")

    def _file_payload(self, path: Path) -> tuple[str, str]:
        size = path.stat().st_size
        if size > self.config.max_bytes:
            raise AppsScriptError(
                f"文件 {path.name} 为 {size} 字节，超过 Apps Script 安全上限 "
                f"{self.config.max_bytes} 字节；文件仍保留在 GitHub Artifact。"
            )
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return encoded, file_sha256(path)

    def upsert_record(self, local_path: Path, record: dict[str, Any]) -> dict[str, Any]:
        encoded, calculated_sha = self._file_payload(local_path)
        extension = str(record.get("format_detected") or local_path.suffix.lstrip("."))
        result = self._post(
            {
                "operation": "upsert",
                "announcement_id": str(record["announcement_id"]),
                "stock_code": str(record.get("stock_code") or ""),
                "publish_date": str(record["publish_date"]),
                "format_detected": extension.lower(),
                "sha256": str(record.get("sha256") or calculated_sha),
                "file_name": local_path.name,
                "mime_type": mime_for(local_path, extension),
                "content_base64": encoded,
            }
        )
        status = str(result.get("status") or "")
        if status not in {"created", "updated", "skipped"}:
            raise AppsScriptError(f"Apps Script 返回未知上传状态：{status!r}")
        return {
            "status": status,
            "file_id": result.get("file_id"),
            "drive_path": result.get("drive_path"),
            "version": result.get("version"),
            "web_view_link": result.get("web_view_link"),
        }

    def upload_run_file(self, local_path: Path, run_id: str) -> dict[str, Any]:
        encoded, sha256 = self._file_payload(local_path)
        result = self._post(
            {
                "operation": "run_file",
                "run_id": run_id,
                "sha256": sha256,
                "file_name": local_path.name,
                "mime_type": mime_for(local_path),
                "content_base64": encoded,
            }
        )
        status = str(result.get("status") or "")
        if status not in {"created", "updated", "skipped"}:
            raise AppsScriptError(f"Apps Script 返回未知清单状态：{status!r}")
        return {
            "status": status,
            "file_id": result.get("file_id"),
            "web_view_link": result.get("web_view_link"),
        }

    def ping(self) -> dict[str, Any]:
        """验证 Web App URL 和令牌，不写入文件。"""
        return self._post({"operation": "ping"})
