"""每日龙虎榜采集、结构化输出与 Google Drive 归档。

数据源为东方财富公开龙虎榜数据接口。只保留沪深 A 股，排除北交所和
可转债。输出 CSV、JSON、Markdown 摘要及本地审计清单；Drive 使用既有
Apps Script Web App，并按交易日期幂等写入 CNINFO/龙虎榜。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from src.apps_script_storage import AppsScriptDriveClient

API_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
SOURCE_PAGE = "https://data.eastmoney.com/stock/tradedetail.html"
REPORT_NAME = "RPT_DAILYBILLBOARD_DETAILSNEW"
BEIJING_TZ = timezone(timedelta(hours=8))
TRANSIENT_STATUS = frozenset({408, 429, 500, 502, 503, 504})
PAGE_SIZE = 500
COLUMNS = ",".join(
    [
        "SECURITY_CODE",
        "SECUCODE",
        "SECURITY_NAME_ABBR",
        "TRADE_DATE",
        "EXPLAIN",
        "CLOSE_PRICE",
        "CHANGE_RATE",
        "BILLBOARD_NET_AMT",
        "BILLBOARD_BUY_AMT",
        "BILLBOARD_SELL_AMT",
        "BILLBOARD_DEAL_AMT",
        "ACCUM_AMOUNT",
        "DEAL_NET_RATIO",
        "DEAL_AMOUNT_RATIO",
        "TURNOVERRATE",
        "FREE_MARKET_CAP",
        "EXPLANATION",
        "D1_CLOSE_ADJCHRATE",
        "D2_CLOSE_ADJCHRATE",
        "D5_CLOSE_ADJCHRATE",
        "D10_CLOSE_ADJCHRATE",
        "SECURITY_TYPE_CODE",
    ]
)
CSV_SCHEMA = [
    ("trade_date", "交易日期"),
    ("security_code", "证券代码"),
    ("security_name", "证券简称"),
    ("market", "市场"),
    ("interpretation", "解读"),
    ("close_price", "收盘价"),
    ("change_rate_pct", "涨跌幅_pct"),
    ("billboard_net_amount", "龙虎榜净买额_元"),
    ("billboard_buy_amount", "龙虎榜买入额_元"),
    ("billboard_sell_amount", "龙虎榜卖出额_元"),
    ("billboard_deal_amount", "龙虎榜成交额_元"),
    ("market_deal_amount", "市场成交额_元"),
    ("net_amount_ratio_pct", "净买额占总成交比_pct"),
    ("deal_amount_ratio_pct", "成交额占总成交比_pct"),
    ("turnover_rate_pct", "换手率_pct"),
    ("free_market_cap", "流通市值_元"),
    ("reason", "上榜原因"),
    ("d1_change_rate_pct", "上榜后1日涨跌幅_pct"),
    ("d2_change_rate_pct", "上榜后2日涨跌幅_pct"),
    ("d5_change_rate_pct", "上榜后5日涨跌幅_pct"),
    ("d10_change_rate_pct", "上榜后10日涨跌幅_pct"),
    ("security_type_code", "证券类型代码"),
]


class DragonTigerError(RuntimeError):
    """龙虎榜数据源、输出或上传失败。"""


@dataclass(frozen=True)
class FetchResult:
    records: list[dict[str, Any]]
    pages: int
    version: str | None


def validate_trade_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise DragonTigerError("交易日期必须是 YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise DragonTigerError("交易日期必须是 YYYY-MM-DD")
    return value


def beijing_today() -> str:
    return datetime.now(BEIJING_TZ).date().isoformat()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


class EastmoneyDragonTigerClient:
    """东方财富每日龙虎榜分页客户端。"""

    def __init__(self, session: requests.Session | None = None, *, timeout: int = 30, retries: int = 5, sleep: Callable[[float], None] = time.sleep) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.retries = retries
        self.sleep = sleep

    def _request_page(self, trade_date: str, page_number: int) -> tuple[list[dict[str, Any]], int, str | None]:
        params = {
            "sortColumns": "SECURITY_CODE,TRADE_DATE", "sortTypes": "1,-1",
            "pageSize": str(PAGE_SIZE), "pageNumber": str(page_number),
            "reportName": REPORT_NAME, "columns": COLUMNS, "source": "WEB", "client": "WEB",
            "filter": f"(TRADE_DATE<='{trade_date}')(TRADE_DATE>='{trade_date}')",
        }
        headers = {"Referer": SOURCE_PAGE, "User-Agent": "Mozilla/5.0 (compatible; stock-archive/1.0)", "Accept": "application/json,text/plain,*/*"}
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                response = self.session.get(API_URL, params=params, headers=headers, timeout=self.timeout)
                if response.status_code in TRANSIENT_STATUS and attempt < self.retries:
                    self.sleep(min(2 ** (attempt - 1), 16)); continue
                if response.status_code >= 400:
                    detail = response.text[:300].replace("\n", " ")
                    raise DragonTigerError(f"东方财富龙虎榜接口 HTTP {response.status_code}: {detail}")
                payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt < self.retries:
                    self.sleep(min(2 ** (attempt - 1), 16)); continue
                raise DragonTigerError("无法获取东方财富龙虎榜数据") from exc
            if not isinstance(payload, dict): raise DragonTigerError("东方财富龙虎榜响应结构异常")
            version = str(payload.get("version")) if payload.get("version") else None
            result = payload.get("result")
            if result is None: return [], 0, version
            if not isinstance(result, dict): raise DragonTigerError("东方财富龙虎榜响应缺少 result")
            data = result.get("data") or []
            if not isinstance(data, list): raise DragonTigerError("东方财富龙虎榜 data 不是列表")
            try: pages = int(result.get("pages") or (1 if data else 0))
            except (TypeError, ValueError) as exc: raise DragonTigerError("东方财富龙虎榜页数格式异常") from exc
            return [item for item in data if isinstance(item, dict)], pages, version
        raise DragonTigerError(f"东方财富龙虎榜请求重试耗尽：{last_error}")

    def fetch(self, trade_date: str) -> FetchResult:
        trade_date = validate_trade_date(trade_date)
        first, pages, version = self._request_page(trade_date, 1)
        records = list(first)
        for page_number in range(2, pages + 1):
            page, _, page_version = self._request_page(trade_date, page_number)
            records.extend(page); version = version or page_version
        unique: list[dict[str, Any]] = []; seen: set[str] = set()
        for record in records:
            fingerprint = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
            if fingerprint not in seen: seen.add(fingerprint); unique.append(record)
        return FetchResult(records=unique, pages=pages, version=version)


def _number(value: Any) -> int | float | None:
    if value is None or value == "" or isinstance(value, bool): return None
    if isinstance(value, (int, float)): return value
    try: number = float(str(value))
    except (TypeError, ValueError): return None
    return int(number) if number.is_integer() else number


def normalize_record(record: dict[str, Any], requested_date: str) -> dict[str, Any] | None:
    code = str(record.get("SECURITY_CODE") or "").strip()
    secucode = str(record.get("SECUCODE") or "").strip().upper()
    if not re.fullmatch(r"\d{6}", code): return None
    if not (secucode.endswith(".SH") or secucode.endswith(".SZ")): return None
    if code[0] not in {"0", "3", "6"}: return None
    source_date = str(record.get("TRADE_DATE") or requested_date)[:10]
    return {
        "trade_date": source_date, "security_code": code, "secu_code": secucode,
        "security_name": str(record.get("SECURITY_NAME_ABBR") or ""),
        "market": "沪市" if secucode.endswith(".SH") else "深市",
        "interpretation": str(record.get("EXPLAIN") or ""),
        "close_price": _number(record.get("CLOSE_PRICE")), "change_rate_pct": _number(record.get("CHANGE_RATE")),
        "billboard_net_amount": _number(record.get("BILLBOARD_NET_AMT")), "billboard_buy_amount": _number(record.get("BILLBOARD_BUY_AMT")),
        "billboard_sell_amount": _number(record.get("BILLBOARD_SELL_AMT")), "billboard_deal_amount": _number(record.get("BILLBOARD_DEAL_AMT")),
        "market_deal_amount": _number(record.get("ACCUM_AMOUNT")), "net_amount_ratio_pct": _number(record.get("DEAL_NET_RATIO")),
        "deal_amount_ratio_pct": _number(record.get("DEAL_AMOUNT_RATIO")), "turnover_rate_pct": _number(record.get("TURNOVERRATE")),
        "free_market_cap": _number(record.get("FREE_MARKET_CAP")), "reason": str(record.get("EXPLANATION") or ""),
        "d1_change_rate_pct": _number(record.get("D1_CLOSE_ADJCHRATE")), "d2_change_rate_pct": _number(record.get("D2_CLOSE_ADJCHRATE")),
        "d5_change_rate_pct": _number(record.get("D5_CLOSE_ADJCHRATE")), "d10_change_rate_pct": _number(record.get("D10_CLOSE_ADJCHRATE")),
        "security_type_code": str(record.get("SECURITY_TYPE_CODE") or ""),
    }


def normalize_records(records: list[dict[str, Any]], requested_date: str) -> tuple[list[dict[str, Any]], int]:
    accepted: list[dict[str, Any]] = []; excluded = 0
    for source_record in records:
        normalized = normalize_record(source_record, requested_date)
        if normalized is None: excluded += 1
        else: accepted.append(normalized)
    accepted.sort(key=lambda item: (item["security_code"], item["reason"], item["billboard_net_amount"] is None, -(float(item["billboard_net_amount"] or 0))))
    return accepted, excluded


def _write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    headers = [label for _, label in CSV_SCHEMA]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, lineterminator="\n"); writer.writeheader()
        for record in records: writer.writerow({label: record.get(key) for key, label in CSV_SCHEMA})


def _format_wan(value: Any) -> str:
    number = _number(value); return "—" if number is None else f"{number / 10000:,.2f}"


def _escape_markdown(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def _write_summary(path: Path, trade_date: str, records: list[dict[str, Any]]) -> None:
    unique_count = len({item["security_code"] for item in records})
    lines = [f"# 每日龙虎榜摘要｜{trade_date}", "", f"- 数据记录：**{len(records)}** 条", f"- 上榜证券：**{unique_count}** 只", "- 市场范围：沪市、深市 A 股（排除北交所和可转债）", f"- 数据来源：[东方财富龙虎榜]({SOURCE_PAGE})", ""]
    ranked = sorted([item for item in records if item["billboard_net_amount"] is not None], key=lambda item: float(item["billboard_net_amount"]), reverse=True)[:10]
    if ranked:
        lines.extend(["## 龙虎榜净买额前 10 条记录", "", "| 代码 | 名称 | 净买额（万元） | 涨跌幅 | 上榜原因 |", "| --- | --- | ---: | ---: | --- |"]) 
        for item in ranked:
            change = item["change_rate_pct"]; change_text = "—" if change is None else f"{float(change):.2f}%"
            lines.append("| {code} | {name} | {amount} | {change} | {reason} |".format(code=item["security_code"], name=_escape_markdown(item["security_name"]), amount=_format_wan(item["billboard_net_amount"]), change=change_text, reason=_escape_markdown(item["reason"])))
    else: lines.extend(["## 当日无龙虎榜数据", "", "该日期可能为非交易日或数据尚未发布。"])
    lines.extend(["", "---", "", "数据仅供归档和研究，不构成投资建议；如与交易所披露不一致，以交易所为准。"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _describe_files(paths: list[Path]) -> list[dict[str, Any]]:
    return [{"name": path.name, "size": path.stat().st_size, "sha256": file_sha256(path)} for path in paths]


def run(*, trade_date: str, output_dir: Path, upload_drive: bool = False, source_client: EastmoneyDragonTigerClient | None = None, drive_client: AppsScriptDriveClient | None = None) -> dict[str, Any]:
    trade_date = validate_trade_date(trade_date); generated_at = datetime.now(BEIJING_TZ).isoformat(timespec="seconds")
    target = Path(output_dir) / trade_date; target.mkdir(parents=True, exist_ok=True); manifest_path = target / "manifest.json"
    try: fetched = (source_client or EastmoneyDragonTigerClient()).fetch(trade_date)
    except Exception as exc:
        summary = {"phase": "failed", "trade_date": trade_date, "generated_at": generated_at, "source": SOURCE_PAGE, "raw": 0, "accepted": 0, "excluded": 0, "unique_securities": 0, "upload_drive_requested": upload_drive, "drive_backend": "not_started", "drive_created": 0, "drive_updated": 0, "drive_skipped": 0, "drive_failed": 0, "failures": 1, "errors": [str(exc)]}
        manifest_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); print(json.dumps(summary, ensure_ascii=False, sort_keys=True)); return summary
    records, excluded = normalize_records(fetched.records, trade_date); unique_count = len({item["security_code"] for item in records})
    csv_path = target / f"龙虎榜_{trade_date}.csv"; json_path = target / f"龙虎榜_{trade_date}.json"; markdown_path = target / f"龙虎榜摘要_{trade_date}.md"
    _write_csv(csv_path, records)
    json_path.write_text(json.dumps({"schema_version": 1, "dataset": "dragon_tiger", "trade_date": trade_date, "generated_at": generated_at, "source": SOURCE_PAGE, "source_report": REPORT_NAME, "source_version": fetched.version, "market_scope": "Shanghai and Shenzhen A-shares; Beijing and bonds excluded", "record_count": len(records), "unique_security_count": unique_count, "records": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_summary(markdown_path, trade_date, records); data_paths = [csv_path, json_path, markdown_path]
    drive_counts = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}; drive_files: list[dict[str, Any]] = []; errors: list[str] = []; backend = "not_requested"
    if upload_drive and not records: backend = "not_requested_empty"
    elif upload_drive:
        backend = "apps_script"; client = drive_client or AppsScriptDriveClient()
        try: client.ping()
        except Exception as exc: errors.append(str(exc))
        if not errors:
            for path in data_paths:
                try:
                    result = client.upload_dataset_file(path, dataset="dragon_tiger", data_date=trade_date); status = str(result["status"]); drive_counts[status] += 1
                    drive_files.append({"name": path.name, "status": status, "drive_path": result.get("drive_path"), "file_id": result.get("file_id")})
                except Exception as exc: drive_counts["failed"] += 1; errors.append(f"{path.name}: {exc}")
    summary = {"phase": "failed" if errors else "complete", "status": "data" if records else "no_data", "trade_date": trade_date, "generated_at": generated_at, "source": SOURCE_PAGE, "source_report": REPORT_NAME, "source_version": fetched.version, "pages": fetched.pages, "raw": len(fetched.records), "accepted": len(records), "excluded": excluded, "unique_securities": unique_count, "upload_drive_requested": upload_drive, "drive_backend": backend, "drive_created": drive_counts["created"], "drive_updated": drive_counts["updated"], "drive_skipped": drive_counts["skipped"], "drive_failed": drive_counts["failed"], "files": _describe_files(data_paths), "drive_files": drive_files, "failures": len(errors), "errors": errors}
    manifest_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); print(json.dumps(summary, ensure_ascii=False, sort_keys=True)); return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="采集并归档每日龙虎榜数据")
    parser.add_argument("--date", default=beijing_today(), help="交易日期 YYYY-MM-DD，默认北京时间今日")
    parser.add_argument("--output", type=Path, default=Path("artifacts/dragon-tiger"), help="本地输出根目录")
    parser.add_argument("--upload-drive", action="store_true", help="通过 Apps Script 上传到 Google Drive")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv); summary = run(trade_date=args.date, output_dir=args.output, upload_drive=args.upload_drive)
    return 1 if summary["failures"] else 0


if __name__ == "__main__": raise SystemExit(main())
