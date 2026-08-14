import csv
import json
from pathlib import Path

from src.dragon_tiger import (
    FetchResult,
    SeatFetchResult,
    normalize_records,
    run,
)


def raw_record(code="000001", secucode="000001.SZ"):
    return {
        "SECURITY_CODE": code,
        "SECUCODE": secucode,
        "SECURITY_NAME_ABBR": "测试股份",
        "TRADE_DATE": "2026-08-11 00:00:00",
        "EXPLAIN": "1家机构买入",
        "CLOSE_PRICE": 12.34,
        "CHANGE_RATE": 10.01,
        "BILLBOARD_NET_AMT": 12345678,
        "BILLBOARD_BUY_AMT": 22345678,
        "BILLBOARD_SELL_AMT": 10000000,
        "BILLBOARD_DEAL_AMT": 32345678,
        "ACCUM_AMOUNT": 100000000,
        "DEAL_NET_RATIO": 12.345,
        "DEAL_AMOUNT_RATIO": 32.345,
        "TURNOVERRATE": 20.5,
        "FREE_MARKET_CAP": 5000000000,
        "EXPLANATION": "日涨幅偏离值达到7%",
        "SECURITY_TYPE_CODE": "058001001",
    }


def raw_seat(side, department, buy, sell):
    return {
        "SECURITY_CODE": "000001",
        "SECUCODE": "000001.SZ",
        "TRADE_DATE": "2026-08-11 00:00:00",
        "OPERATEDEPT_CODE": "10001",
        "OPERATEDEPT_NAME": department,
        "EXPLANATION": "日涨幅偏离值达到7%",
        "BUY": buy,
        "SELL": sell,
        "NET": buy - sell,
        "TOTAL_BUYRIO": 0.1,
        "TOTAL_SELLRIO": 0.05,
        "TRADE_ID": "trade-1",
        "CHANGE_TYPE": "change-1",
        "_seat_side": side,
    }


def test_market_filter():
    accepted, excluded = normalize_records(
        [
            raw_record(),
            raw_record("688001", "688001.SH"),
            raw_record("832001", "832001.BJ"),
            raw_record("123001", "123001.SZ"),
        ],
        "2026-08-11",
    )
    assert [item["security_code"] for item in accepted] == ["000001", "688001"]
    assert excluded == 2


class Source:
    def fetch(self, trade_date):
        return FetchResult([raw_record()], 1, "summary-test")

    def fetch_seats(self, trade_date, security_codes):
        assert security_codes == ["000001"]
        return SeatFetchResult(
            [
                raw_seat("buy", "买一营业部", 20000000, 1000000),
                raw_seat("sell", "卖一营业部", 2000000, 18000000),
            ],
            2,
            {"buy": "buy-test", "sell": "sell-test"},
        )


class Drive:
    def __init__(self):
        self.calls = []

    def ping(self):
        return {"service": "test"}

    def upload_dataset_file(self, path, *, dataset, data_date):
        self.calls.append(path.name)
        return {
            "status": "created",
            "file_id": str(len(self.calls)),
            "drive_path": path.name,
        }


def _first_csv_row(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return next(csv.DictReader(handle))


def test_run_writes_six_digit_codes_and_seat_details(tmp_path: Path):
    drive = Drive()
    result = run(
        trade_date="2026-08-11",
        output_dir=tmp_path,
        upload_drive=True,
        source_client=Source(),
        drive_client=drive,
    )
    target = tmp_path / "2026-08-11"
    assert result["phase"] == "complete"
    assert result["drive_created"] == 4
    assert result["drive_failed"] == 0
    assert result["seat_records"] == 2
    assert len(drive.calls) == 4

    summary_row = _first_csv_row(target / "龙虎榜_2026-08-11.csv")
    seat_rows_path = target / "龙虎榜席位明细_2026-08-11.csv"
    with seat_rows_path.open(encoding="utf-8-sig", newline="") as handle:
        seat_rows = list(csv.DictReader(handle))
    assert summary_row["证券代码"] == '="000001"'
    assert {row["证券代码"] for row in seat_rows} == {'="000001"'}
    assert {row["营业部名称"] for row in seat_rows} == {"买一营业部", "卖一营业部"}

    payload = json.loads((target / "龙虎榜_2026-08-11.json").read_text())
    assert payload["schema_version"] == 2
    assert payload["records"][0]["security_code"] == "000001"
    assert payload["records"][0]["buy_seats"][0]["department_name"] == "买一营业部"
    assert payload["records"][0]["sell_seats"][0]["department_name"] == "卖一营业部"

    report = (target / "龙虎榜摘要_2026-08-11.md").read_text()
    assert "000001｜测试股份" in report
    assert "买入前五席位" in report
    assert "卖出前五席位" in report
