"""HTTP 工具：限速、重试、JSONP 解包、磁盘缓存。

仅在 akshare 不可用时兜底直连公开接口时使用。
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # requests 属于运行期依赖，离线单测时允许缺失
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore[assignment]

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Connection": "keep-alive",
}


class FetchError(RuntimeError):
    """网络抓取失败。"""


@dataclass
class Http:
    """极简 HTTP 客户端。

    - ``min_interval``：两次请求之间的最小间隔（秒），避免被限流
    - ``retries``：失败重试次数，指数退避 + 抖动
    - ``cache_dir``：非空时把响应文本落盘缓存，``ttl`` 秒内复用
    """

    min_interval: float = 0.35
    timeout: float = 20.0
    retries: int = 3
    cache_dir: Path | None = None
    ttl: float = 6 * 3600
    headers: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HEADERS))
    _session: Any = field(default=None, init=False, repr=False)
    _last_call: float = field(default=0.0, init=False, repr=False)

    @property
    def session(self) -> Any:
        if requests is None:  # pragma: no cover
            raise FetchError("缺少 requests 依赖，无法直连兜底接口：pip install requests")
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(self.headers)
        return self._session

    # ---------------- 内部工具 ----------------
    def _throttle(self) -> None:
        gap = time.time() - self._last_call
        wait = self.min_interval - gap
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.15))
        self._last_call = time.time()

    def _cache_file(self, key: str) -> Path | None:
        if not self.cache_dir:
            return None
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return self.cache_dir / f"{digest}.txt"

    def _cache_read(self, key: str) -> str | None:
        path = self._cache_file(key)
        if not path or not path.exists():
            return None
        if self.ttl and time.time() - path.stat().st_mtime > self.ttl:
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover
            return None

    def _cache_write(self, key: str, text: str) -> None:
        path = self._cache_file(key)
        if not path:
            return
        try:
            path.write_text(text, encoding="utf-8")
        except OSError:  # pragma: no cover
            pass

    # ---------------- 对外接口 ----------------
    def get_text(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        referer: str | None = None,
        extra_headers: dict[str, str] | None = None,
        cache: bool = True,
    ) -> str:
        key = json.dumps([url, params or {}], sort_keys=True, ensure_ascii=False)
        if cache:
            cached = self._cache_read(key)
            if cached is not None:
                return cached

        headers: dict[str, str] = {}
        if referer:
            headers["Referer"] = referer
        if extra_headers:
            headers.update(extra_headers)

        last_error: Exception | None = None
        for attempt in range(1, max(1, self.retries) + 1):
            self._throttle()
            try:
                response = self.session.get(url, params=params, headers=headers or None, timeout=self.timeout)
                if response.status_code >= 400:
                    raise FetchError(f"HTTP {response.status_code} {url}")
                response.encoding = response.apparent_encoding or "utf-8"
                text = response.text
                if not text.strip():
                    raise FetchError(f"空响应 {url}")
                if cache:
                    self._cache_write(key, text)
                return text
            except Exception as exc:  # noqa: BLE001 - 统一转成 FetchError
                last_error = exc
                if attempt < max(1, self.retries):
                    time.sleep(min(8.0, 0.8 * 2 ** (attempt - 1)) + random.uniform(0, 0.4))
        raise FetchError(f"{url} 抓取失败: {type(last_error).__name__}: {last_error}")

    def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        referer: str | None = None,
        extra_headers: dict[str, str] | None = None,
        jsonp: bool = False,
        cache: bool = True,
    ) -> Any:
        text = self.get_text(
            url,
            params,
            referer=referer,
            extra_headers=extra_headers,
            cache=cache,
        )
        return parse_json(text, jsonp=jsonp, source=url)


def parse_json(text: str, *, jsonp: bool = False, source: str = "") -> Any:
    """解析 JSON / JSONP 文本。"""
    payload = text.strip()
    if jsonp or (payload and payload[0] not in "[{"):
        start = payload.find("(")
        end = payload.rfind(")")
        if start != -1 and end > start:
            payload = payload[start + 1 : end]
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise FetchError(f"{source} 返回非法 JSON: {exc}") from exc
