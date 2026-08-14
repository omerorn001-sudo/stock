"""每日龙虎榜采集、席位明细输出与 Google Drive 归档。

数据源为东方财富公开龙虎榜接口。只保留沪深 A 股，排除北交所和可转债；
同时获取每只股票、每个上榜原因对应的买入和卖出前五席位。
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
SEAT_REPORTS = {
    "buy": ("RPT_BILLBOARD_DAILYDETAILSBUY", "BUY"),
    "sell": ("RPT_BILLBOARD_DAILYDETAILSSELL", "SELL"),
}
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
    ("secu_code", "市场代码"),
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
SEAT_CSV_SCHEMA = [
    ("trade_date", "交易日期"),
    ("security_code", "证券代码"),
    ("secu_code", "市场代码"),
    ("security_name", "证券简称"),
    ("market", "市场"),
    ("reason", "上榜原因"),
    ("side_label", "席位方向"),
    ("rank", "排名"),
    ("department_code", "营业部代码"),
    ("department_name", "营业部名称"),
    ("buy_amount", "买入金额_元"),
    ("sell_amount", "卖出金额_元"),
    ("net_amount", "净买额_元"),
    ("buy_ratio_pct", "占总成交买入比_pct"),
    ("sell_ratio_pct", "占总成交卖出比_pct"),
    ("trade_id", "榜单交易ID"),
    ("change_type", "上榜类型代码"),
]


class DragonTigerError(RuntimeError):
    """龙虎榜数据源、输出或上传失败。"""


@dataclass(frozen=True)
class FetchResult:
    records: list[dict[str, Any]]
    pages: int
    version: str | None


@dataclass(frozen=True)
class SeatFetchResult:
    records: list[dict[str, Any]]
    requests: int
    versions: dict[str, str | None]


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


def _deduplicate(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        fingerprint = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        if fingerprint not in seen:
            seen.add(fingerprint)
            unique.append(record)
    return unique


class EastmoneyDragonTigerClient:
    """东方财富龙虎榜汇总及买卖席位明细客户端。"""

    def __init__(
        self,
        session: requests.Session | None = None,
        *,
        timeout: int = 30,
        retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.retries = retries
        self.sleep = sleep

    def _request_json(self, params: dict[str, str], label: str) -> dict[str, Any]:
        headers = {
            "Referer": SOURCE_PAGE,
            "User-Agent": "Mozilla/5.0 (compatible; stock-archive/1.0)",
            "Accept": "application/json,text/plain,*/*",
        }
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                response = self.session.get(
                    API_URL,
                    params=params,
                    headers=headers,
                    timeout=self.timeout,
                )
                if response.status_code in TRANSIENT_STATUS and attempt < self.retries:
                    self.sleep(min(2 ** (attempt - 1), 16))
                    continue
                if response.status_code >= 400:
                    detail = response.text[:300].replace("\n", " ")
                    raise DragonTigerError(
                        f"东方财富{label}接口 HTTP {response.status_code}: {detail}"
                    )
                payload = response.json()
            except DragonTigerError:
                raise
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt < self.retries:
                    self.sleep(min(2 ** (attempt - 1), 16))
                    continue
                raise DragonTigerError(f"无法获取东方财富{label}数据") from exc
            if not isinstance(payload, dict):
                raise DragonTigerError(f"东方财富{label}响应结构异常")
            if payload.get("success") is False:
                raise DragonTigerError(
                    f"东方财富{label}接口失败：{payload.get('message') or 'unknown'}"
                )
            return payload
        raise DragonTigerError(f"东方财富{label}请求重试耗尽：{last_error}")

    @staticmethod
    def _decode_page(
        payload: dict[str, Any], label: str
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        version = str(payload.get("version")) if payload.get("version") else None
        result = payload.get("result")
        if result is None:
            return [], 0, version
        if not isinstance(result, dict):
            raise DragonTigerError(f"东方财富{label}响应缺少 result")
        data = result.get("data") or []
        if not isinstance(data, list):
            raise DragonTigerError(f"东方财富{label} data 不是列表")
        try:
            pages = int(result.get("pages") or (1 if data else 0))
        except (TypeError, ValueError) as exc:
            raise DragonTigerError(f"东方财富{label}页数格式异常") from exc
        return [item for item in data if isinstance(item, dict)], pages, version

    def _request_page(
        self, trade_date: str, page_number: int
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        params = {
            "sortColumns": "SECURITY_CODE,TRADE_DATE",
            "sortTypes": "1,-1",
            "pageSize": str(PAGE_SIZE),
            "pageNumber": str(page_number),
            "reportName": REPORT_NAME,
            "columns": COLUMNS,
            "source": "WEB",
            "client": "WEB",
            "filter": f"(TRADE_DATE<='{trade_date}')(TRADE_DATE>='{trade_date}')",
        }
        return self._decode_page(self._request_json(params, "龙虎榜汇总"), "龙虎榜汇总")

    def _request_seat_page(
        self,
        trade_date: str,
        security_code: str,
        side: str,
        page_number: int,
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        report_name, sort_column = SEAT_REPORTS[side]
        params = {
            "sortColumns": sort_column,
            "sortTypes": "-1",
            "pageSize": str(PAGE_SIZE),
            "pageNumber": str(page_number),
            "reportName": report_name,
            "columns": "ALL",
            "source": "WEB",
            "client": "WEB",
            "filter": (
                f"(TRADE_DATE='{trade_date}')"
                f'(SECURITY_CODE="{security_code}")'
            ),
        }
        label = "买入席位" if side == "buy" else "卖出席位"
        return self._decode_page(self._request_json(params, label), label)

    def fetch(self, trade_date: str) -> FetchResult:
        trade_date = validate_trade_date(trade_date)
        first, pages, version = self._request_page(trade_date, 1)
        records = list(first)
        for page_number in range(2, pages + 1):
            page, _, page_version = self._request_page(trade_date, page_number)
            records.extend(page)
            version = version or page_version
        return FetchResult(records=_deduplicate(records), pages=pages, version=version)

    def fetch_seats(
        self, trade_date: str, security_codes: list[str]
    ) -> SeatFetchResult:
        trade_date = validate_trade_date(trade_date)
        records: list[dict[str, Any]] = []
        request_count = 0
        versions: dict[str, str | None] = {"buy": None, "sell": None}
        for security_code in sorted(set(security_codes)):
            if not re.fullmatch(r"\d{6}", security_code):
                raise DragonTigerError(f"证券代码格式异常：{security_code}")
            for side in ("buy", "sell"):
                page_number = 1
                while True:
                    page, pages, version = self._request_seat_page(
                        trade_date,
                        security_code,
                        side,
                        page_number,
                    )
                    request_count += 1
                    versions[side] = versions[side] or version
                    for source_record in page:
                        copied = dict(source_record)
                        copied["_seat_side"] = side
                        records.append(copied)
                    if page_number >= pages:
                        break
                    page_number += 1
        return SeatFetchResult(
            records=_deduplicate(records),
            requests=request_count,
            versions=versions,
        )


def _number(value: Any) -> int | float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _ratio_pct(value: Any) -> int | float | None:
    number = _number(value)
    if number is None:
        return None
    return number * 100


def normalize_record(
    record: dict[str, Any], requested_date: str
) -> dict[str, Any] | None:
    code = str(record.get("SECURITY_CODE") or "").strip()
    secucode = str(record.get("SECUCODE") or "").strip().upper()
    if not re.fullmatch(r"\d{6}", code):
        return None
    if not (secucode.endswith(".SH") or secucode.endswith(".SZ")):
        return None
    if code[0] not in {"0", "3", "6"}:
        return None
    source_date = str(record.get("TRADE_DATE") or requested_date)[:10]
    return {
        "trade_date": source_date,
        "security_code": code,
        "secu_code": secucode,
        "security_name": str(record.get("SECURITY_NAME_ABBR") or ""),
        "market": "沪市" if secucode.endswith(".SH") else "深市",
        "interpretation": str(record.get("EXPLAIN") or ""),
        "close_price": _number(record.get("CLOSE_PRICE")),
        "change_rate_pct": _number(record.get("CHANGE_RATE")),
        "billboard_net_amount": _number(record.get("BILLBOARD_NET_AMT")),
        "billboard_buy_amount": _number(record.get("BILLBOARD_BUY_AMT")),
        "billboard_sell_amount": _number(record.get("BILLBOARD_SELL_AMT")),
        "billboard_deal_amount": _number(record.get("BILLBOARD_DEAL_AMT")),
        "market_deal_amount": _number(record.get("ACCUM_AMOUNT")),
        "net_amount_ratio_pct": _number(record.get("DEAL_NET_RATIO")),
        "deal_amount_ratio_pct": _number(record.get("DEAL_AMOUNT_RATIO")),
        "turnover_rate_pct": _number(record.get("TURNOVERRATE")),
        "free_market_cap": _number(record.get("FREE_MARKET_CAP")),
        "reason": str(record.get("EXPLANATION") or ""),
        "d1_change_rate_pct": _number(record.get("D1_CLOSE_ADJCHRATE")),
        "d2_change_rate_pct": _number(record.get("D2_CLOSE_ADJCHRATE")),
        "d5_change_rate_pct": _number(record.get("D5_CLOSE_ADJCHRATE")),
        "d10_change_rate_pct": _number(record.get("D10_CLOSE_ADJCHRATE")),
        "security_type_code": str(record.get("SECURITY_TYPE_CODE") or ""),
    }


def normalize_records(
    records: list[dict[str, Any]], requested_date: str
) -> tuple[list[dict[str, Any]], int]:
    accepted: list[dict[str, Any]] = []
    excluded = 0
    for source_record in records:
        normalized = normalize_record(source_record, requested_date)
        if normalized is None:
            excluded += 1
        else:
            accepted.append(normalized)
    accepted.sort(
        key=lambda item: (
            item["security_code"],
            item["reason"],
            item["billboard_net_amount"] is None,
            -(float(item["billboard_net_amount"] or 0)),
        )
    )
    return accepted, excluded


def _normalize_seat_record(
    record: dict[str, Any],
    requested_date: str,
    summary_by_code: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    code = str(record.get("SECURITY_CODE") or "").strip()
    secucode = str(record.get("SECUCODE") or "").strip().upper()
    side = str(record.get("_seat_side") or "").strip()
    if code not in summary_by_code or side not in {"buy", "sell"}:
        return None
    if not re.fullmatch(r"\d{6}", code):
        return None
    if not (secucode.endswith(".SH") or secucode.endswith(".SZ")):
        return None
    summary = summary_by_code[code]
    return {
        "trade_date": str(record.get("TRADE_DATE") or requested_date)[:10],
        "security_code": code,
        "secu_code": secucode,
        "security_name": summary["security_name"],
        "market": summary["market"],
        "reason": str(record.get("EXPLANATION") or ""),
        "side": side,
        "side_label": "买入席位" if side == "buy" else "卖出席位",
        "rank": 0,
        "department_code": str(record.get("OPERATEDEPT_CODE") or ""),
        "department_name": str(record.get("OPERATEDEPT_NAME") or ""),
        "buy_amount": _number(record.get("BUY")),
        "sell_amount": _number(record.get("SELL")),
        "net_amount": _number(record.get("NET")),
        "buy_ratio_pct": _ratio_pct(record.get("TOTAL_BUYRIO")),
        "sell_ratio_pct": _ratio_pct(record.get("TOTAL_SELLRIO")),
        "trade_id": str(record.get("TRADE_ID") or ""),
        "change_type": str(record.get("CHANGE_TYPE") or ""),
    }


def normalize_seats(
    records: list[dict[str, Any]],
    summary_records: list[dict[str, Any]],
    requested_date: str,
) -> list[dict[str, Any]]:
    summary_by_code: dict[str, dict[str, Any]] = {}
    valid_reasons: set[tuple[str, str]] = set()
    for summary in summary_records:
        summary_by_code.setdefault(summary["security_code"], summary)
        valid_reasons.add((summary["security_code"], summary["reason"]))

    accepted: list[dict[str, Any]] = []
    for source_record in records:
        normalized = _normalize_seat_record(
            source_record,
            requested_date,
            summary_by_code,
        )
        if normalized is None:
            continue
        if (normalized["security_code"], normalized["reason"]) not in valid_reasons:
            continue
        accepted.append(normalized)

    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for item in accepted:
        key = (item["security_code"], item["reason"], item["side"])
        groups.setdefault(key, []).append(item)

    ranked: list[dict[str, Any]] = []
    for key in sorted(groups):
        side = key[2]
        amount_key = "buy_amount" if side == "buy" else "sell_amount"
        group = sorted(
            groups[key],
            key=lambda item: (
                item[amount_key] is None,
                -(float(item[amount_key] or 0)),
                item["department_name"],
            ),
        )[:5]
        for rank, item in enumerate(group, start=1):
            ranked.append({**item, "rank": rank})
    ranked.sort(
        key=lambda item: (
            item["security_code"],
            item["reason"],
            0 if item["side"] == "buy" else 1,
            item["rank"],
        )
    )
    return ranked


def _csv_cell(key: str, value: Any) -> Any:
    if key == "security_code" and re.fullmatch(r"\d{6}", str(value or "")):
        return f'="{value}"'
    return value


def _write_csv(
    path: Path,
    records: list[dict[str, Any]],
    schema: list[tuple[str, str]],
) -> None:
    headers = [label for _, label in schema]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    label: _csv_cell(key, record.get(key))
                    for key, label in schema
                }
            )


def _format_wan(value: Any) -> str:
    number = _number(value)
    return "—" if number is None else f"{number / 10000:,.2f}"


def _format_pct(value: Any) -> str:
    number = _number(value)
    return "—" if number is None else f"{float(number):.2f}%"


def _escape_markdown(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def _append_seat_table(
    lines: list[str],
    title: str,
    seats: list[dict[str, Any]],
) -> None:
    lines.extend(
        [
            f"#### {title}",
            "",
            "| 排名 | 营业部/机构 | 买入（万元） | 卖出（万元） | 净额（万元） |",
            "| ---: | --- | ---: | ---: | ---: |",
        ]
    )
    if not seats:
        lines.append("| — | 未披露 | — | — | — |")
    else:
        for seat in seats:
            lines.append(
                "| {rank} | {name} | {buy} | {sell} | {net} |".format(
                    rank=seat["rank"],
                    name=_escape_markdown(seat["department_name"]),
                    buy=_format_wan(seat["buy_amount"]),
                    sell=_format_wan(seat["sell_amount"]),
                    net=_format_wan(seat["net_amount"]),
                )
            )
    lines.append("")


def _write_summary(
    path: Path,
    trade_date: str,
    records: list[dict[str, Any]],
    seats: list[dict[str, Any]],
) -> None:
    unique_count = len({item["security_code"] for item in records})
    lines = [
        f"# 每日龙虎榜完整报告｜{trade_date}",
        "",
        f"- 龙虎榜记录：**{len(records)}** 条",
        f"- 上榜证券：**{unique_count}** 只",
        f"- 买卖席位记录：**{len(seats)}** 条",
        "- 市场范围：沪市、深市 A 股（排除北交所和可转债）",
        f"- 数据来源：[东方财富龙虎榜]({SOURCE_PAGE})",
        "",
    ]
    seat_index: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for seat in seats:
        key = (seat["security_code"], seat["reason"], seat["side"])
        seat_index.setdefault(key, []).append(seat)

    records_by_code: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        records_by_code.setdefault(record["security_code"], []).append(record)

    for code in sorted(records_by_code):
        stock_records = records_by_code[code]
        first = stock_records[0]
        lines.extend(
            [
                f"## {code}｜{_escape_markdown(first['security_name'])}",
                "",
            ]
        )
        for record in stock_records:
            reason = record["reason"]
            lines.extend(
                [
                    f"### 上榜原因：{_escape_markdown(reason)}",
                    "",
                    f"- 收盘价：{record['close_price'] if record['close_price'] is not None else '—'}",
                    f"- 涨跌幅：{_format_pct(record['change_rate_pct'])}",
                    f"- 龙虎榜买入额：{_format_wan(record['billboard_buy_amount'])} 万元",
                    f"- 龙虎榜卖出额：{_format_wan(record['billboard_sell_amount'])} 万元",
                    f"- 龙虎榜净买额：{_format_wan(record['billboard_net_amount'])} 万元",
                    "",
                ]
            )
            _append_seat_table(
                lines,
                "买入前五席位",
                seat_index.get((code, reason, "buy"), []),
            )
            _append_seat_table(
                lines,
                "卖出前五席位",
                seat_index.get((code, reason, "sell"), []),
            )

    if not records:
        lines.extend(
            [
                "## 当日无龙虎榜数据",
                "",
                "该日期可能为非交易日或数据尚未发布。",
            ]
        )
    lines.extend(
        [
            "---",
            "",
            "数据仅供归档和研究，不构成投资建议；如与交易所披露不一致，以交易所为准。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _enrich_records(
    records: list[dict[str, Any]],
    seats: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seat_index: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for seat in seats:
        key = (seat["security_code"], seat["reason"], seat["side"])
        seat_index.setdefault(key, []).append(seat)
    enriched: list[dict[str, Any]] = []
    for record in records:
        key_base = (record["security_code"], record["reason"])
        enriched.append(
            {
                **record,
                "buy_seats": seat_index.get((*key_base, "buy"), []),
                "sell_seats": seat_index.get((*key_base, "sell"), []),
            }
        )
    return enriched


def _describe_files(paths: list[Path]) -> list[dict[str, Any]]:
    return [
        {
            "name": path.name,
            "size": path.stat().st_size,
            "sha256": file_sha256(path),
        }
        for path in paths
    ]


def run(
    *,
    trade_date: str,
    output_dir: Path,
    upload_drive: bool = False,
    source_client: EastmoneyDragonTigerClient | None = None,
    drive_client: AppsScriptDriveClient | None = None,
) -> dict[str, Any]:
    trade_date = validate_trade_date(trade_date)
    generated_at = datetime.now(BEIJING_TZ).isoformat(timespec="seconds")
    target = Path(output_dir) / trade_date
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / "manifest.json"
    source = source_client or EastmoneyDragonTigerClient()
    fetched: FetchResult | None = None
    seat_fetched = SeatFetchResult(records=[], requests=0, versions={})
    records: list[dict[str, Any]] = []
    seats: list[dict[str, Any]] = []
    excluded = 0
    try:
        fetched = source.fetch(trade_date)
        records, excluded = normalize_records(fetched.records, trade_date)
        if records:
            fetch_seats = getattr(source, "fetch_seats", None)
            if not callable(fetch_seats):
                raise DragonTigerError("数据客户端不支持买卖席位明细")
            seat_fetched = fetch_seats(
                trade_date,
                sorted({item["security_code"] for item in records}),
            )
            seats = normalize_seats(seat_fetched.records, records, trade_date)
    except Exception as exc:
        summary = {
            "phase": "failed",
            "trade_date": trade_date,
            "generated_at": generated_at,
            "source": SOURCE_PAGE,
            "raw": len(fetched.records) if fetched else 0,
            "accepted": len(records),
            "excluded": excluded,
            "unique_securities": len({item["security_code"] for item in records}),
            "seat_records": len(seats),
            "upload_drive_requested": upload_drive,
            "drive_backend": "not_started",
            "drive_created": 0,
            "drive_updated": 0,
            "drive_skipped": 0,
            "drive_failed": 0,
            "failures": 1,
            "errors": [str(exc)],
        }
        manifest_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return summary

    unique_codes = {item["security_code"] for item in records}
    codes_with_seats = {item["security_code"] for item in seats}
    missing_codes = sorted(unique_codes - codes_with_seats)
    unique_count = len(unique_codes)
    csv_path = target / f"龙虎榜_{trade_date}.csv"
    seat_csv_path = target / f"龙虎榜席位明细_{trade_date}.csv"
    json_path = target / f"龙虎榜_{trade_date}.json"
    markdown_path = target / f"龙虎榜摘要_{trade_date}.md"

    _write_csv(csv_path, records, CSV_SCHEMA)
    _write_csv(seat_csv_path, seats, SEAT_CSV_SCHEMA)
    json_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "dataset": "dragon_tiger",
                "trade_date": trade_date,
                "source": SOURCE_PAGE,
                "source_reports": [REPORT_NAME, *[item[0] for item in SEAT_REPORTS.values()]],
                "market_scope": "Shanghai and Shenzhen A-shares; Beijing and bonds excluded",
                "record_count": len(records),
                "seat_record_count": len(seats),
                "unique_security_count": unique_count,
                "securities_without_seat_details": missing_codes,
                "records": _enrich_records(records, seats),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_summary(markdown_path, trade_date, records, seats)
    data_paths = [csv_path, seat_csv_path, json_path, markdown_path]

    drive_counts = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
    drive_files: list[dict[str, Any]] = []
    errors: list[str] = []
    backend = "not_requested"
    if upload_drive and not records:
        backend = "not_requested_empty"
    elif upload_drive:
        backend = "apps_script"
        client = drive_client or AppsScriptDriveClient()
        try:
            client.ping()
        except Exception as exc:
            errors.append(str(exc))
        if not errors:
            for path in data_paths:
                try:
                    result = client.upload_dataset_file(
                        path,
                        dataset="dragon_tiger",
                        data_date=trade_date,
                    )
                    status = str(result["status"])
                    drive_counts[status] += 1
                    drive_files.append(
                        {
                            "name": path.name,
                            "status": status,
                            "drive_path": result.get("drive_path"),
                            "file_id": result.get("file_id"),
                        }
                    )
                except Exception as exc:
                    drive_counts["failed"] += 1
                    errors.append(f"{path.name}: {exc}")

    summary = {
        "phase": "failed" if errors else "complete",
        "status": "data" if records else "no_data",
        "trade_date": trade_date,
        "generated_at": generated_at,
        "source": SOURCE_PAGE,
        "source_report": REPORT_NAME,
        "source_version": fetched.version,
        "seat_source_versions": seat_fetched.versions,
        "pages": fetched.pages,
        "seat_requests": seat_fetched.requests,
        "raw": len(fetched.records),
        "accepted": len(records),
        "excluded": excluded,
        "unique_securities": unique_count,
        "seat_records": len(seats),
        "securities_with_seats": len(codes_with_seats),
        "securities_without_seats": missing_codes,
        "upload_drive_requested": upload_drive,
        "drive_backend": backend,
        "drive_created": drive_counts["created"],
        "drive_updated": drive_counts["updated"],
        "drive_skipped": drive_counts["skipped"],
        "drive_failed": drive_counts["failed"],
        "files": _describe_files(data_paths),
        "drive_files": drive_files,
        "failures": len(errors),
        "errors": errors,
    }
    manifest_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="采集并归档每日龙虎榜及买卖席位明细")
    parser.add_argument(
        "--date",
        default=beijing_today(),
        help="交易日期 YYYY-MM-DD，默认北京时间今日",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/dragon-tiger"),
        help="本地输出根目录",
    )
    parser.add_argument(
        "--upload-drive",
        action="store_true",
        help="通过 Apps Script 上传到 Google Drive",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run(
        trade_date=args.date,
        output_dir=args.output,
        upload_drive=args.upload_drive,
    )
    return 1 if summary["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
