"""第一阶段：巨潮机构调研公告发现与原始附件接收。

阶段边界：
- 只处理沪市、深市 A 股，明确排除北交所；
- 发现公告并下载 PDF、DOC、DOCX 原件；
- 生成可审计的 JSON/CSV 清单和失败清单；
- 不上传 Google Drive，不启用定时任务，不做正文结构化解析。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import random
import re
import sys
import time
import zipfile
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
STATIC_BASE = "http://static.cninfo.com.cn/"
PAGE_SIZE = 30
MAX_PAGES = 400
TIMEOUT_SEC = 25
MAX_ATTEMPTS = 5
MIN_INTERVAL_SEC = float(os.getenv("JUCHAO_MIN_INTERVAL", "0.8"))
JITTER_SEC = 0.4
TZ = ZoneInfo("Asia/Shanghai")

KEYWORDS = ("投资者关系活动记录表", "机构调研")
TITLE_PATTERNS = ("投资者关系活动记录", "机构调研", "调研活动信息")
ALLOWED_MARKETS = frozenset({"sh", "sz"})
SUPPORTED_FORMATS = frozenset({"pdf", "doc", "docx"})
ALLOWED_ATTACHMENT_HOSTS = frozenset(
    {"static.cninfo.com.cn", "dataclouds.cninfo.com.cn", "www.cninfo.com.cn"}
)

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Origin": "http://www.cninfo.com.cn",
    "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
    "X-Requested-With": "XMLHttpRequest",
}

HTML_TAG_RE = re.compile(r"<[^>]+>")
TITLE_RE = re.compile("|".join(re.escape(p) for p in TITLE_PATTERNS))
INVALID_FILENAME_RE = re.compile(r"[\\/:*?\"<>|\x00-\x1f]+")
OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
MANIFEST_FIELDS = (
    "schema_version", "announcement_id", "stock_code", "company_name", "market",
    "title", "publish_date", "attachment_url", "source_filename", "format_hint",
    "format_detected", "mime_detected", "size_bytes", "sha256", "local_path",
    "download_status", "format_mismatch", "source_keyword", "fetched_at",
)

log = logging.getLogger("juchao.phase1")


class TransientError(RuntimeError):
    """可重试的网络或服务端错误。"""


class PermanentError(RuntimeError):
    """不可重试的请求、来源或文件错误。"""


class RateLimiter:
    def __init__(self, minimum: float = MIN_INTERVAL_SEC, jitter: float = JITTER_SEC) -> None:
        self.minimum = minimum
        self.jitter = jitter
        self._last = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        wait_for = self.minimum - elapsed + random.uniform(0, self.jitter)
        if wait_for > 0:
            time.sleep(wait_for)
        self._last = time.monotonic()


_limiter = RateLimiter()


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"非法日期 {value!r}，应为 YYYY-MM-DD") from exc


def month_slices(start: date, end: date) -> Iterator[tuple[date, date]]:
    if start > end:
        raise ValueError("开始日期不得晚于结束日期")
    current = start
    while current <= end:
        next_month = (
            date(current.year + 1, 1, 1)
            if current.month == 12
            else date(current.year, current.month + 1, 1)
        )
        yield current, min(end, next_month - timedelta(days=1))
        current = next_month


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(REQUEST_HEADERS)
    return session


def build_payload(start: date, end: date, keyword: str, page: int) -> dict[str, Any]:
    return {
        "pageNum": page, "pageSize": PAGE_SIZE, "column": "", "tabName": "fulltext",
        "plate": "", "stock": "", "searchkey": keyword, "secid": "", "category": "",
        "trade": "", "seDate": f"{start.isoformat()}~{end.isoformat()}",
        "sortName": "", "sortType": "", "isHLtitle": "true",
    }


@retry(
    retry=retry_if_exception_type((TransientError, requests.RequestException)),
    stop=stop_after_attempt(MAX_ATTEMPTS),
    wait=wait_exponential(multiplier=1.5, min=2, max=45),
    reraise=True,
)
def query_page(
    session: requests.Session, start: date, end: date, keyword: str, page: int
) -> dict[str, Any]:
    _limiter.wait()
    response = session.post(
        QUERY_URL, data=build_payload(start, end, keyword, page), timeout=TIMEOUT_SEC
    )
    if response.status_code in {408, 429, 500, 502, 503, 504}:
        raise TransientError(f"公告查询 HTTP {response.status_code}")
    if response.status_code >= 400:
        raise PermanentError(f"公告查询 HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise TransientError("公告查询返回非 JSON，可能触发限流") from exc
    if not isinstance(payload, dict):
        raise TransientError("公告查询响应结构异常")
    return payload


def market_from_code(code: str) -> str:
    if not re.fullmatch(r"\d{6}", code or ""):
        return "other"
    if code.startswith("6"):
        return "sh"
    if code.startswith(("0", "3")):
        return "sz"
    if code.startswith(("4", "8", "920")):
        return "bj"
    return "other"


def _clean_title(value: str | None) -> str:
    return HTML_TAG_RE.sub("", value or "").strip()


def _publish_date(value: int | float | None) -> str | None:
    if value in (None, "", 0):
        return None
    return datetime.fromtimestamp(float(value) / 1000, TZ).strftime("%Y-%m-%d")


def resolve_attachment_url(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    url = raw if urlparse(raw).scheme else urljoin(STATIC_BASE, raw.lstrip("/"))
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    if (parsed.hostname or "").lower() not in ALLOWED_ATTACHMENT_HOSTS:
        return None
    return url


def format_hint_from_url(url: str) -> str:
    suffix = PurePosixPath(unquote(urlparse(url).path)).suffix.lower().lstrip(".")
    return suffix if suffix in SUPPORTED_FORMATS else "unknown"


def normalize_announcement(
    announcement: dict[str, Any], keyword: str, market_filter: str = "all"
) -> tuple[dict[str, Any] | None, str | None]:
    title = _clean_title(announcement.get("announcementTitle"))
    if not TITLE_RE.search(title):
        return None, "title"
    code = str(announcement.get("secCode") or "").strip()
    market = market_from_code(code)
    if market not in ALLOWED_MARKETS:
        return None, "market"
    if market_filter != "all" and market != market_filter:
        return None, "market_filter"
    ann_id = str(announcement.get("announcementId") or "").strip()
    publish_date = _publish_date(announcement.get("announcementTime"))
    attachment_url = resolve_attachment_url(announcement.get("adjunctUrl"))
    if not ann_id or not publish_date or not attachment_url:
        return None, "metadata"
    source_filename = PurePosixPath(unquote(urlparse(attachment_url).path)).name
    return (
        {
            "schema_version": "phase1-1.0", "announcement_id": ann_id,
            "stock_code": code, "company_name": str(announcement.get("secName") or "").strip(),
            "market": market, "title": title, "publish_date": publish_date,
            "attachment_url": attachment_url, "source_filename": source_filename,
            "format_hint": format_hint_from_url(attachment_url), "format_detected": None,
            "mime_detected": None, "size_bytes": None, "sha256": None,
            "local_path": None, "download_status": "not_requested",
            "format_mismatch": False, "source_keyword": keyword, "fetched_at": now_iso(),
        },
        None,
    )


def iter_query(
    session: requests.Session, start: date, end: date, keyword: str
) -> Iterator[dict[str, Any]]:
    for page in range(1, MAX_PAGES + 1):
        payload = query_page(session, start, end, keyword, page)
        announcements = payload.get("announcements") or []
        if not isinstance(announcements, list):
            raise TransientError("announcements 字段不是数组")
        if not announcements:
            return
        yield from announcements
        if not payload.get("hasMore"):
            return
    raise PermanentError(f"达到 MAX_PAGES={MAX_PAGES}，分页可能被截断")


def fetch_range(
    start: date,
    end: date,
    market_filter: str = "all",
    session: requests.Session | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    if start > end:
        raise ValueError("开始日期不得晚于结束日期")
    if market_filter not in {"all", "sh", "sz"}:
        raise ValueError("market_filter 只能为 all、sh 或 sz")
    sess = session or make_session()
    records: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    stats = {
        "raw": 0, "accepted": 0, "excluded_market": 0,
        "excluded_market_filter": 0, "excluded_title": 0,
        "excluded_metadata": 0, "duplicates": 0,
    }
    for slice_start, slice_end in month_slices(start, end):
        for keyword in KEYWORDS:
            try:
                for raw in iter_query(sess, slice_start, slice_end, keyword):
                    stats["raw"] += 1
                    record, reason = normalize_announcement(raw, keyword, market_filter)
                    if record is None:
                        key = {
                            "market": "excluded_market", "market_filter": "excluded_market_filter",
                            "title": "excluded_title", "metadata": "excluded_metadata",
                        }.get(reason or "", "excluded_metadata")
                        stats[key] += 1
                        continue
                    if record["announcement_id"] in records:
                        stats["duplicates"] += 1
                        continue
                    records[record["announcement_id"]] = record
            except Exception as exc:  # noqa: BLE001
                failures.append({
                    "stage": "query", "start": slice_start.isoformat(),
                    "end": slice_end.isoformat(), "keyword": keyword,
                    "error": f"{type(exc).__name__}: {exc}", "at": now_iso(),
                })
    stats["accepted"] = len(records)
    rows = sorted(
        records.values(), key=lambda item: (item["publish_date"], item["announcement_id"])
    )
    return rows, failures, stats


def sniff_format(path: Path) -> str | None:
    with path.open("rb") as handle:
        head = handle.read(8192)
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(OLE_MAGIC):
        return "doc"
    if head.startswith(b"PK"):
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
            if "word/document.xml" in names and "[Content_Types].xml" in names:
                return "docx"
        except zipfile.BadZipFile:
            return None
    return None


def sanitize_filename(value: str, limit: int = 48) -> str:
    cleaned = INVALID_FILENAME_RE.sub("_", value).strip(" ._")
    cleaned = re.sub(r"\s+", "_", cleaned)
    return (cleaned or "untitled")[:limit]


def archive_filename(record: dict[str, Any], detected_format: str) -> str:
    parts = (
        sanitize_filename(record["stock_code"], 8),
        sanitize_filename(record["company_name"], 24),
        sanitize_filename(record["publish_date"], 10),
        sanitize_filename(record["announcement_id"], 24),
        sanitize_filename(record["title"], 48),
    )
    return "_".join(parts) + f".{detected_format}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


@retry(
    retry=retry_if_exception_type((TransientError, requests.RequestException)),
    stop=stop_after_attempt(MAX_ATTEMPTS),
    wait=wait_exponential(multiplier=1.5, min=2, max=45),
    reraise=True,
)
def download_attachment(
    record: dict[str, Any], output_dir: Path, session: requests.Session | None = None
) -> Path:
    url = resolve_attachment_url(record.get("attachment_url"))
    if not url:
        raise PermanentError("附件 URL 不在巨潮官方白名单")
    sess = session or make_session()
    _limiter.wait()
    response = sess.get(url, timeout=TIMEOUT_SEC, stream=True, allow_redirects=True)
    if response.status_code in {408, 429, 500, 502, 503, 504}:
        raise TransientError(f"附件下载 HTTP {response.status_code}")
    if response.status_code >= 400:
        raise PermanentError(f"附件下载 HTTP {response.status_code}")
    if not resolve_attachment_url(response.url):
        raise PermanentError("附件重定向到了非巨潮官方域名")
    temp_dir = output_dir / ".tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp = temp_dir / f"{record['announcement_id']}.part"
    try:
        with temp.open("wb") as handle:
            for chunk in response.iter_content(1 << 16):
                if chunk:
                    handle.write(chunk)
        if temp.stat().st_size == 0:
            raise PermanentError("附件为空文件")
        detected = sniff_format(temp)
        record["mime_detected"] = response.headers.get("Content-Type", "").split(";", 1)[0]
        record["size_bytes"] = temp.stat().st_size
        record["sha256"] = _sha256(temp)
        record["format_detected"] = detected
        record["format_mismatch"] = bool(
            detected and record.get("format_hint") not in {None, "unknown", detected}
        )
        if detected not in SUPPORTED_FORMATS:
            quarantine = output_dir / "quarantine" / f"{record['announcement_id']}.bin"
            quarantine.parent.mkdir(parents=True, exist_ok=True)
            temp.replace(quarantine)
            record["download_status"] = "quarantined"
            record["local_path"] = str(quarantine.relative_to(output_dir))
            raise PermanentError("附件不是受支持的 PDF、DOC 或 DOCX")
        target = (
            output_dir / "files" / record["publish_date"][:4] /
            record["publish_date"][:7] / archive_filename(record, detected)
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        temp.replace(target)
        record["download_status"] = "ok"
        record["local_path"] = str(target.relative_to(output_dir))
        return target
    finally:
        temp.unlink(missing_ok=True)


def write_outputs(
    output_dir: Path,
    records: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        json.dumps({"summary": summary, "records": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with (output_dir / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    (output_dir / "failures.json").write_text(
        json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def write_step_summary(summary: dict[str, Any]) -> None:
    target = os.getenv("GITHUB_STEP_SUMMARY")
    if not target:
        return
    lines = [
        "## 巨潮机构调研｜第一阶段", "",
        f"- 日期：`{summary['start']}` ～ `{summary['end']}`",
        f"- 市场：`{summary['market']}`（仅沪深 A 股，北交所已排除）",
        f"- 接收候选：**{summary['accepted']}**",
        f"- 排除北交所／其他市场：**{summary['excluded_market']}**",
        f"- 下载成功：**{summary['downloaded']}**",
        f"- PDF / DOC / DOCX：**{summary['pdf']} / {summary['doc']} / {summary['docx']}**",
        f"- 失败：**{summary['failures']}**",
    ]
    with Path(target).open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="巨潮机构调研第一阶段：发现与原始附件接收")
    parser.add_argument("--start", required=True, type=parse_date, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, type=parse_date, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--market", choices=("all", "sh", "sz"), default="all")
    parser.add_argument("--download-files", action="store_true", help="下载 PDF、DOC、DOCX 原件")
    parser.add_argument("--max-files", type=int, default=0, help="最多下载数量；0 表示不限制")
    parser.add_argument("--output", type=Path, default=Path("artifacts/juchao-phase1"))
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    if args.start > args.end:
        raise SystemExit("开始日期不得晚于结束日期")
    if (args.end - args.start).days > 31:
        raise SystemExit("第一阶段单次区间不得超过 31 天，请按月拆分")
    if args.max_files < 0:
        raise SystemExit("--max-files 不得为负数")
    session = make_session()
    records, failures, query_stats = fetch_range(args.start, args.end, args.market, session)
    selected = records[: args.max_files] if args.max_files else records
    if args.download_files:
        for record in selected:
            try:
                download_attachment(record, args.output, session)
            except Exception as exc:  # noqa: BLE001
                if record["download_status"] == "not_requested":
                    record["download_status"] = "failed"
                failures.append({
                    "stage": "download", "announcement_id": record["announcement_id"],
                    "attachment_url": record["attachment_url"],
                    "error": f"{type(exc).__name__}: {exc}", "at": now_iso(),
                })
    format_counts = {name: 0 for name in SUPPORTED_FORMATS}
    for record in records:
        detected = record.get("format_detected")
        if detected in format_counts:
            format_counts[detected] += 1
    summary = {
        "phase": 1, "generated_at": now_iso(), "start": args.start.isoformat(),
        "end": args.end.isoformat(), "market": args.market, **query_stats,
        "download_requested": bool(args.download_files), "download_limit": args.max_files,
        "downloaded": sum(r["download_status"] == "ok" for r in records),
        "quarantined": sum(r["download_status"] == "quarantined" for r in records),
        "pdf": format_counts["pdf"], "doc": format_counts["doc"],
        "docx": format_counts["docx"], "failures": len(failures),
    }
    write_outputs(args.output, records, failures, summary)
    write_step_summary(summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
