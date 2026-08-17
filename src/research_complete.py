"""机构调研完整采集：汇总清单对账、原始附件下载和 Drive 归档。"""
from __future__ import annotations

import hashlib
import re
import sys
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from . import phase1, pipeline

EASTMONEY_API = "https://" + "datacenter-web.eastmoney.com/api/data/v1/get"
EASTMONEY_PAGE = "https://" + "data.eastmoney.com/jgdy/tj.html"
SUMMARY_REPORT = "RPT_ORG_SURVEYNEW"
DETAIL_REPORT = "RPT_ORG_SURVEY"
PAGE_SIZE = 50
TRANSIENT_STATUS = frozenset({408, 429, 500, 502, 503, 504})
ATTACHMENT_HOSTS = frozenset({"pdf.dfcfw.com"})
SOURCE_ID_RE = re.compile(r"AN\d{10,}", re.IGNORECASE)
SUMMARY_COLUMNS = (
    "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,NOTICE_DATE,"
    "RECEIVE_START_DATE,RECEIVE_END_DATE,RECEIVE_TIME_EXPLAIN,"
    "RECEIVE_WAY_EXPLAIN,IS_SOURCE,NUMBERNEW"
)


def iter_dates(start: date, end: date) -> Iterator[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def dataset_params(report: str, day: date, page: int) -> dict[str, str]:
    if report == SUMMARY_REPORT:
        filter_value = (
            f'(NUMBERNEW="1")(IS_SOURCE="1")'
            f"(NOTICE_DATE='{day.isoformat()}')"
        )
        columns = SUMMARY_COLUMNS
        sort_columns = "SECURITY_CODE,RECEIVE_START_DATE"
        sort_types = "1,1"
    elif report == DETAIL_REPORT:
        filter_value = f"(NOTICE_DATE='{day.isoformat()}')"
        columns = "ALL"
        sort_columns = "SECURITY_CODE,RECEIVE_START_DATE,NUMBERNEW"
        sort_types = "1,1,1"
    else:
        raise ValueError(f"不支持的报表：{report}")
    return {
        "reportName": report,
        "columns": columns,
        "filter": filter_value,
        "pageNumber": str(page),
        "pageSize": str(PAGE_SIZE),
        "sortColumns": sort_columns,
        "sortTypes": sort_types,
        "source": "WEB",
        "client": "WEB",
    }


@retry(
    retry=retry_if_exception_type((phase1.TransientError, requests.RequestException)),
    stop=stop_after_attempt(phase1.MAX_ATTEMPTS),
    wait=wait_exponential(multiplier=1.5, min=2, max=45),
    reraise=True,
)
def query_page(
    session: requests.Session, report: str, day: date, page: int
) -> dict[str, Any]:
    phase1._limiter.wait()
    response = session.get(
        EASTMONEY_API,
        params=dataset_params(report, day, page),
        headers={
            "Referer": EASTMONEY_PAGE,
            "User-Agent": phase1.REQUEST_HEADERS["User-Agent"],
            "Accept": "application/json, text/plain, */*",
        },
        timeout=phase1.TIMEOUT_SEC,
    )
    if response.status_code in TRANSIENT_STATUS:
        raise phase1.TransientError(f"东方财富 {report} HTTP {response.status_code}")
    if response.status_code >= 400:
        raise phase1.PermanentError(f"东方财富 {report} HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise phase1.TransientError(f"东方财富 {report} 返回非 JSON") from exc
    if not isinstance(payload, dict) or payload.get("success") is False:
        message = payload.get("message") if isinstance(payload, dict) else "结构异常"
        raise phase1.TransientError(f"东方财富 {report} 响应异常：{message}")
    return payload


def iter_dataset(
    session: requests.Session, report: str, day: date
) -> Iterator[dict[str, Any]]:
    page, pages = 1, 1
    while page <= pages:
        result = query_page(session, report, day, page).get("result")
        if result is None:
            return
        if not isinstance(result, dict):
            raise phase1.TransientError(f"东方财富 {report} result 结构异常")
        data = result.get("data") or []
        if not isinstance(data, list):
            raise phase1.TransientError(f"东方财富 {report} data 不是数组")
        yield from (item for item in data if isinstance(item, dict))
        try:
            pages = int(result.get("pages") or 0)
        except (TypeError, ValueError) as exc:
            raise phase1.TransientError(f"东方财富 {report} 页数异常") from exc
        page += 1


def fetch_summary_date(day: date, session: requests.Session) -> list[dict[str, Any]]:
    return list(iter_dataset(session, SUMMARY_REPORT, day))


def fetch_detail_date(day: date, session: requests.Session) -> list[dict[str, Any]]:
    return list(iter_dataset(session, DETAIL_REPORT, day))


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _date_part(value: Any) -> str:
    return _text(value)[:10]


def activity_key(item: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        _text(item.get("SECURITY_CODE")),
        _date_part(item.get("RECEIVE_START_DATE")),
        _date_part(item.get("RECEIVE_END_DATE")),
        _text(item.get("RECEIVE_TIME_EXPLAIN")),
        _text(item.get("RECEIVE_WAY_EXPLAIN")),
    )


def source_id(item: dict[str, Any]) -> str:
    match = SOURCE_ID_RE.search(_text(item.get("URL")))
    return match.group(0).upper() if match else ""


def attachment_url(announcement_id: str) -> str:
    if not SOURCE_ID_RE.fullmatch(announcement_id):
        raise ValueError(f"非法东方财富公告 ID：{announcement_id!r}")
    return (
        "https://" + "pdf.dfcfw.com/pdf/H2_" + announcement_id + "_1.pdf"
    )


def record_title(item: dict[str, Any]) -> str:
    activity = _text(item.get("RECEIVE_TIME_EXPLAIN")) or _date_part(
        item.get("RECEIVE_START_DATE")
    )
    way = _text(item.get("RECEIVE_WAY_EXPLAIN"))
    values = [value for value in (activity, way, "投资者关系活动记录表") if value]
    return "_".join(dict.fromkeys(values))


def normalize_detail(
    item: dict[str, Any], market_filter: str = "all"
) -> tuple[dict[str, Any] | None, str | None]:
    if _text(item.get("IS_SOURCE")) != "1":
        return None, "not_source"
    code = _text(item.get("SECURITY_CODE"))
    market = phase1.market_from_code(code)
    if market not in phase1.ALLOWED_MARKETS:
        return None, "market"
    if market_filter != "all" and market != market_filter:
        return None, "market_filter"
    announcement_id = source_id(item)
    publish_date = _date_part(item.get("NOTICE_DATE"))
    if not announcement_id or not publish_date:
        return None, "metadata"
    hint = _text(item.get("FILE_EXTENSION")).lower()
    hint = hint if hint in phase1.SUPPORTED_FORMATS else "unknown"
    url = attachment_url(announcement_id)
    return (
        {
            "schema_version": "research-complete-2.1",
            "announcement_id": announcement_id,
            "stock_code": code,
            "company_name": _text(item.get("SECURITY_NAME_ABBR")),
            "market": market,
            "title": record_title(item),
            "publish_date": publish_date,
            "attachment_url": url,
            "source_filename": f"{announcement_id}.{hint}",
            "format_hint": hint,
            "format_detected": None,
            "mime_detected": None,
            "size_bytes": None,
            "sha256": None,
            "local_path": None,
            "download_status": "not_requested",
            "format_mismatch": False,
            "source_keyword": "eastmoney:RPT_ORG_SURVEY",
            "source_system": "eastmoney",
            "source_announcement_id": announcement_id,
            "activity_key": "|".join(activity_key(item)),
            "fetched_at": phase1.now_iso(),
        },
        None,
    )


def archive_filename(record: dict[str, Any], detected_format: str) -> str:
    """公告 ID 只用于隐藏去重元数据，不进入文件名。"""
    parts = (
        phase1.sanitize_filename(str(record["stock_code"]), 8),
        phase1.sanitize_filename(str(record["company_name"]), 24),
        phase1.sanitize_filename(str(record["publish_date"]), 10),
        phase1.sanitize_filename(str(record["title"]), 64),
    )
    return "_".join(parts) + f".{detected_format}"


def _selected(item: dict[str, Any], market_filter: str) -> bool:
    market = phase1.market_from_code(_text(item.get("SECURITY_CODE")))
    return market in phase1.ALLOWED_MARKETS and (
        market_filter == "all" or market == market_filter
    )


def expected_item(item: dict[str, Any], day: date) -> dict[str, str]:
    key = activity_key(item)
    return {
        "publish_date": day.isoformat(),
        "stock_code": key[0],
        "company_name": _text(item.get("SECURITY_NAME_ABBR")),
        "receive_start_date": key[1],
        "receive_end_date": key[2],
        "receive_time": key[3],
        "receive_way": key[4],
        "activity_key": "|".join(key),
    }


def fetch_range(
    start: date,
    end: date,
    market_filter: str = "all",
    session: requests.Session | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if start > end:
        raise ValueError("开始日期不得晚于结束日期")
    if market_filter not in {"all", "sh", "sz"}:
        raise ValueError("market_filter 只能为 all、sh 或 sz")
    sess = session or phase1.make_session()
    records: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    expected_activities: dict[tuple[str, tuple[str, ...]], dict[str, str]] = {}
    actual_activities: set[tuple[str, tuple[str, ...]]] = set()
    expected_companies: dict[tuple[str, str], str] = {}
    actual_companies: set[tuple[str, str]] = set()
    stats: dict[str, Any] = {
        "raw": 0,
        "summary_raw": 0,
        "detail_raw": 0,
        "accepted": 0,
        "excluded_market": 0,
        "excluded_market_filter": 0,
        "excluded_not_source": 0,
        "excluded_metadata": 0,
        "duplicates": 0,
    }
    for day in iter_dates(start, end):
        try:
            summary_rows = fetch_summary_date(day, sess)
        except Exception as exc:  # noqa: BLE001
            failures.append({
                "stage": "expected_query",
                "date": day.isoformat(),
                "source": SUMMARY_REPORT,
                "error": f"{type(exc).__name__}: {exc}",
                "at": phase1.now_iso(),
            })
            summary_rows = []
        stats["summary_raw"] += len(summary_rows)
        for item in summary_rows:
            if _text(item.get("IS_SOURCE")) != "1" or _text(item.get("NUMBERNEW")) != "1":
                continue
            if not _selected(item, market_filter):
                continue
            code = _text(item.get("SECURITY_CODE"))
            expected_companies.setdefault(
                (day.isoformat(), code),
                _text(item.get("SECURITY_NAME_ABBR")),
            )
            key = activity_key(item)
            expected_activities.setdefault(
                (day.isoformat(), key), expected_item(item, day)
            )
        try:
            detail_rows = fetch_detail_date(day, sess)
        except Exception as exc:  # noqa: BLE001
            failures.append({
                "stage": "detail_query",
                "date": day.isoformat(),
                "source": DETAIL_REPORT,
                "error": f"{type(exc).__name__}: {exc}",
                "at": phase1.now_iso(),
            })
            detail_rows = []
        stats["detail_raw"] += len(detail_rows)
        stats["raw"] += len(detail_rows)
        for item in detail_rows:
            record, reason = normalize_detail(item, market_filter)
            if record is None:
                key_name = {
                    "market": "excluded_market",
                    "market_filter": "excluded_market_filter",
                    "not_source": "excluded_not_source",
                    "metadata": "excluded_metadata",
                }.get(reason or "", "excluded_metadata")
                stats[key_name] += 1
                continue
            actual_activities.add((day.isoformat(), activity_key(item)))
            actual_companies.add((day.isoformat(), str(record["stock_code"])))
            announcement_id = str(record["announcement_id"])
            if announcement_id in records:
                stats["duplicates"] += 1
            else:
                records[announcement_id] = record
    missing_company_keys = sorted(set(expected_companies) - actual_companies)
    missing_companies = [
        {
            "publish_date": day_value,
            "stock_code": code,
            "company_name": expected_companies[(day_value, code)],
        }
        for day_value, code in missing_company_keys
    ]
    missing_activity_keys = sorted(set(expected_activities) - actual_activities)
    missing_activities = [expected_activities[key] for key in missing_activity_keys]
    if missing_companies or missing_activities:
        failures.append({
            "stage": "completeness",
            "missing_company_count": len(missing_companies),
            "missing_activity_count": len(missing_activities),
            "missing_companies": missing_companies,
            "missing_activities": missing_activities,
            "error": "预期清单未全部匹配到可下载的原始公告附件",
            "at": phase1.now_iso(),
        })
    matched_companies = len(set(expected_companies) & actual_companies)
    matched_activities = len(set(expected_activities) & actual_activities)
    stats.update({
        "accepted": len(records),
        "expected_companies": len(expected_companies),
        "matched_expected_companies": matched_companies,
        "missing_expected_companies": len(missing_companies),
        "missing_expected": missing_companies,
        "expected_records": len(expected_activities),
        "matched_expected_records": matched_activities,
        "missing_expected_records": len(missing_activities),
        "missing_expected_record_list": missing_activities,
        "coverage_pct": (
            round(100 * matched_companies / len(expected_companies), 2)
            if expected_companies else 100.0
        ),
        "record_coverage_pct": (
            round(100 * matched_activities / len(expected_activities), 2)
            if expected_activities else 100.0
        ),
    })
    rows = sorted(
        records.values(),
        key=lambda item: (
            str(item["publish_date"]),
            str(item["stock_code"]),
            str(item["announcement_id"]),
        ),
    )
    return rows, failures, stats


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def collision_safe_target(target: Path, sha256: str) -> Path:
    if not target.exists() or sha256_file(target) == sha256:
        return target
    for index in range(2, 1000):
        candidate = target.with_name(f"{target.stem}_{index}{target.suffix}")
        if not candidate.exists() or sha256_file(candidate) == sha256:
            return candidate
    raise phase1.PermanentError(f"同名文件过多：{target.name}")


@retry(
    retry=retry_if_exception_type((phase1.TransientError, requests.RequestException)),
    stop=stop_after_attempt(phase1.MAX_ATTEMPTS),
    wait=wait_exponential(multiplier=1.5, min=2, max=45),
    reraise=True,
)
def download_attachment(
    record: dict[str, Any], output_dir: Path, session: requests.Session | None = None
) -> Path:
    url = str(record.get("attachment_url") or "")
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ATTACHMENT_HOSTS:
        raise phase1.PermanentError("附件 URL 不在东方财富官方附件域名")
    sess = session or phase1.make_session()
    phase1._limiter.wait()
    response = sess.get(
        url,
        headers={
            "User-Agent": phase1.REQUEST_HEADERS["User-Agent"],
            "Referer": EASTMONEY_PAGE,
            "Accept": "application/pdf,application/msword,"
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document,*/*;q=0.8",
        },
        timeout=phase1.TIMEOUT_SEC,
        stream=True,
        allow_redirects=True,
    )
    if response.status_code in TRANSIENT_STATUS:
        raise phase1.TransientError(f"东方财富附件下载 HTTP {response.status_code}")
    if response.status_code >= 400:
        raise phase1.PermanentError(f"东方财富附件下载 HTTP {response.status_code}")
    if (urlparse(response.url).hostname or "").lower() not in ATTACHMENT_HOSTS:
        raise phase1.PermanentError("附件重定向到了非东方财富官方附件域名")
    temp_dir = output_dir / ".tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp = temp_dir / f"{phase1.sanitize_filename(str(record['announcement_id']))}.part"
    try:
        with temp.open("wb") as handle:
            for chunk in response.iter_content(1 << 16):
                if chunk:
                    handle.write(chunk)
        if temp.stat().st_size == 0:
            raise phase1.PermanentError("附件为空文件")
        detected = phase1.sniff_format(temp)
        record["mime_detected"] = response.headers.get("Content-Type", "").split(";", 1)[0]
        record["size_bytes"] = temp.stat().st_size
        record["sha256"] = sha256_file(temp)
        record["format_detected"] = detected
        hint = str(record.get("format_hint") or "unknown")
        record["format_mismatch"] = bool(detected and hint not in {"unknown", detected})
        if detected not in phase1.SUPPORTED_FORMATS:
            quarantine = output_dir / "quarantine" / f"{record['announcement_id']}.bin"
            quarantine.parent.mkdir(parents=True, exist_ok=True)
            temp.replace(quarantine)
            record["download_status"] = "quarantined"
            record["local_path"] = str(quarantine.relative_to(output_dir))
            raise phase1.PermanentError("附件不是受支持的 PDF、DOC 或 DOCX")
        target = (
            output_dir / "files" / str(record["publish_date"])[:4]
            / str(record["publish_date"])[:7]
            / archive_filename(record, str(detected))
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target = collision_safe_target(target, str(record["sha256"]))
        temp.replace(target)
        record["download_status"] = "ok"
        record["local_path"] = str(target.relative_to(output_dir))
        return target
    finally:
        temp.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    phase1.fetch_range = fetch_range
    phase1.download_attachment = download_attachment
    phase1.archive_filename = archive_filename
    return pipeline.main(argv)


if __name__ == "__main__":
    sys.exit(main())
