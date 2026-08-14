import json
from pathlib import Path

from src.dragon_tiger_html import run


class Drive:
    def ping(self):
        return {"service": "test"}

    def upload_dataset_file(self, path, *, dataset, data_date):
        assert dataset == "dragon_tiger"
        assert path.suffix == ".html"
        return {
            "status": "created",
            "file_id": "html-1",
            "drive_path": f"CNINFO/龙虎榜/{data_date}/{path.name}",
        }


def test_html_report_is_searchable_and_contains_seats(tmp_path: Path):
    trade_date = "2026-08-11"
    target = tmp_path / trade_date
    target.mkdir()
    record = {
        "security_code": "000001",
        "secu_code": "000001.SZ",
        "security_name": "测试股份",
        "market": "深市",
        "reason": "日涨幅偏离值达到7%",
        "close_price": 12.34,
        "change_rate_pct": 10.01,
        "billboard_buy_amount": 20000000,
        "billboard_sell_amount": 10000000,
        "billboard_net_amount": 10000000,
        "turnover_rate_pct": 20.5,
        "buy_seats": [
            {
                "rank": 1,
                "department_name": "买一营业部",
                "buy_amount": 10000000,
                "sell_amount": 100000,
                "net_amount": 9900000,
                "buy_ratio_pct": 10,
                "sell_ratio_pct": 0.1,
            }
        ],
        "sell_seats": [
            {
                "rank": 1,
                "department_name": "卖一营业部",
                "buy_amount": 100000,
                "sell_amount": 9000000,
                "net_amount": -8900000,
                "buy_ratio_pct": 0.1,
                "sell_ratio_pct": 9,
            }
        ],
    }
    (target / f"龙虎榜_{trade_date}.json").write_text(
        json.dumps(
            {
                "trade_date": trade_date,
                "source": "https://example.test",
                "records": [record],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (target / "manifest.json").write_text(
        json.dumps(
            {
                "phase": "complete",
                "files": [],
                "drive_created": 0,
                "drive_updated": 0,
                "drive_skipped": 0,
                "drive_failed": 0,
                "drive_files": [],
                "failures": 0,
                "errors": [],
            }
        ),
        encoding="utf-8",
    )

    result = run(
        trade_date=trade_date,
        output_dir=tmp_path,
        upload_drive=True,
        drive_client=Drive(),
    )
    html_path = target / f"龙虎榜报告_{trade_date}.html"
    html = html_path.read_text(encoding="utf-8")
    manifest = json.loads((target / "manifest.json").read_text())

    assert result["phase"] == "complete"
    assert result["drive_status"] == "created"
    assert html.startswith("<!doctype html>")
    assert 'data-stock-code="000001"' in html
    assert "搜索代码、名称、上榜原因或营业部" in html
    assert "买入前五席位" in html and "卖出前五席位" in html
    assert "买一营业部" in html and "卖一营业部" in html
    assert "applySearch" in html
    assert manifest["drive_created"] == 1
    assert manifest["files"][0]["name"] == html_path.name
