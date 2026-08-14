"""把龙虎榜结构化数据渲染为可搜索、可折叠的单文件 HTML 报告。"""

from __future__ import annotations

import argparse
import hashlib
import json
from html import escape
from pathlib import Path
from typing import Any

from src.apps_script_storage import AppsScriptDriveClient

SOURCE_PAGE = "https://data.eastmoney.com/stock/tradedetail.html"


def _number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _money(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "—"
    absolute = abs(number)
    if absolute >= 100_000_000:
        return f"{number / 100_000_000:,.2f} 亿"
    return f"{number / 10_000:,.2f} 万"


def _price(value: Any) -> str:
    number = _number(value)
    return "—" if number is None else f"{number:,.2f}"


def _percent(value: Any) -> str:
    number = _number(value)
    return "—" if number is None else f"{number:,.2f}%"


def _tone(value: Any) -> str:
    number = _number(value)
    if number is None or number == 0:
        return "neutral"
    return "positive" if number > 0 else "negative"


def _text(value: Any) -> str:
    return escape(str(value or "—"))


def _attribute(value: Any) -> str:
    return escape(str(value or ""), quote=True)


def _seat_table(title: str, seats: list[dict[str, Any]], side: str) -> str:
    rows: list[str] = []
    for seat in seats:
        rows.append(
            """
            <tr data-seat-row data-side="{side}">
              <td class="rank">{rank}</td>
              <td class="department">{department}</td>
              <td>{buy}</td>
              <td>{sell}</td>
              <td class="money {tone}">{net}</td>
              <td>{buy_ratio}</td>
              <td>{sell_ratio}</td>
            </tr>
            """.format(
                side=side,
                rank=_text(seat.get("rank")),
                department=_text(seat.get("department_name")),
                buy=_money(seat.get("buy_amount")),
                sell=_money(seat.get("sell_amount")),
                tone=_tone(seat.get("net_amount")),
                net=_money(seat.get("net_amount")),
                buy_ratio=_percent(seat.get("buy_ratio_pct")),
                sell_ratio=_percent(seat.get("sell_ratio_pct")),
            )
        )
    if not rows:
        rows.append(
            '<tr><td colspan="7" class="empty-row">未披露席位明细</td></tr>'
        )
    return """
    <section class="seat-panel {side}">
      <h4><span class="side-dot"></span>{title}</h4>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>排名</th><th>营业部 / 机构</th><th>买入</th><th>卖出</th>
              <th>净额</th><th>买入占比</th><th>卖出占比</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </section>
    """.format(side=side, title=_text(title), rows="".join(rows))


def _metric(label: str, value: str, tone: str = "neutral") -> str:
    return (
        f'<div class="metric"><span>{escape(label)}</span>'
        f'<strong class="{tone}">{escape(value)}</strong></div>'
    )


def _reason_block(record: dict[str, Any]) -> str:
    reason = str(record.get("reason") or "未说明上榜原因")
    buy_seats = list(record.get("buy_seats") or [])
    sell_seats = list(record.get("sell_seats") or [])
    metrics = "".join(
        [
            _metric("收盘价", _price(record.get("close_price"))),
            _metric(
                "涨跌幅",
                _percent(record.get("change_rate_pct")),
                _tone(record.get("change_rate_pct")),
            ),
            _metric("榜单买入", _money(record.get("billboard_buy_amount"))),
            _metric("榜单卖出", _money(record.get("billboard_sell_amount"))),
            _metric(
                "榜单净额",
                _money(record.get("billboard_net_amount")),
                _tone(record.get("billboard_net_amount")),
            ),
            _metric("换手率", _percent(record.get("turnover_rate_pct"))),
        ]
    )
    return """
    <article class="reason-card">
      <h3>{reason}</h3>
      <div class="metrics">{metrics}</div>
      <div class="seat-grid">
        {buy_table}
        {sell_table}
      </div>
    </article>
    """.format(
        reason=_text(reason),
        metrics=metrics,
        buy_table=_seat_table("买入前五席位", buy_seats, "buy"),
        sell_table=_seat_table("卖出前五席位", sell_seats, "sell"),
    )


def build_html(
    *,
    trade_date: str,
    records: list[dict[str, Any]],
    source_page: str = SOURCE_PAGE,
) -> str:
    records_by_code: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        code = str(record.get("security_code") or "")
        records_by_code.setdefault(code, []).append(record)

    seat_count = sum(
        len(record.get("buy_seats") or []) + len(record.get("sell_seats") or [])
        for record in records
    )
    cards: list[str] = []
    for index, code in enumerate(sorted(records_by_code)):
        stock_records = records_by_code[code]
        first = stock_records[0]
        name = str(first.get("security_name") or "")
        market = str(first.get("market") or "")
        all_seats = [
            seat
            for record in stock_records
            for seat in [
                *(record.get("buy_seats") or []),
                *(record.get("sell_seats") or []),
            ]
        ]
        search_terms = [
            code,
            str(first.get("secu_code") or ""),
            name,
            market,
            *(str(record.get("reason") or "") for record in stock_records),
            *(str(seat.get("department_name") or "") for seat in all_seats),
        ]
        reason_blocks = "".join(_reason_block(record) for record in stock_records)
        open_attribute = " open" if index == 0 else ""
        cards.append(
            """
            <section class="stock-card" data-stock-code="{code}" data-search="{search}">
              <details{open_attribute}>
                <summary>
                  <span class="stock-identity">
                    <strong class="stock-code">{code}</strong>
                    <strong class="stock-name">{name}</strong>
                    <span class="market-badge">{market}</span>
                  </span>
                  <span class="stock-glance">
                    <span>{reason_count} 个上榜原因</span>
                    <span class="{change_tone}">{change}</span>
                    <span class="chevron">⌄</span>
                  </span>
                </summary>
                <div class="stock-body">{reasons}</div>
              </details>
            </section>
            """.format(
                code=_text(code),
                search=_attribute(" ".join(search_terms).lower()),
                open_attribute=open_attribute,
                name=_text(name),
                market=_text(market),
                reason_count=len(stock_records),
                change_tone=_tone(first.get("change_rate_pct")),
                change=_percent(first.get("change_rate_pct")),
                reasons=reason_blocks,
            )
        )

    empty = ""
    if not cards:
        empty = '<div class="empty-state">该日期没有龙虎榜数据。</div>'

    head = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>每日龙虎榜完整报告</title>
<style>
:root{--bg:#f4f6f9;--panel:#fff;--text:#172033;--muted:#667085;--line:#e5e9f0;--brand:#2446a8;--brand2:#5474d8;--positive:#cf1322;--negative:#16803c;--buy:#fff3f1;--sell:#eef8f2;--shadow:0 8px 28px rgba(27,39,73,.08)}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",Arial,sans-serif;line-height:1.55}
a{color:var(--brand)}.hero{padding:40px max(22px,calc((100vw - 1440px)/2));color:#fff;background:linear-gradient(135deg,#15265d 0%,#2f54c6 55%,#627fe2 100%)}
.hero-kicker{font-size:13px;letter-spacing:.16em;opacity:.75}.hero h1{margin:8px 0 5px;font-size:clamp(28px,4vw,44px)}.hero-date{font-size:18px;opacity:.9}.overview{display:grid;grid-template-columns:repeat(3,minmax(140px,1fr));gap:12px;margin-top:26px;max-width:720px}.overview-card{padding:16px 18px;border:1px solid rgba(255,255,255,.2);border-radius:14px;background:rgba(255,255,255,.1);backdrop-filter:blur(8px)}.overview-card span{display:block;font-size:12px;opacity:.75}.overview-card strong{display:block;margin-top:3px;font-size:25px}
.toolbar{position:sticky;top:0;z-index:20;display:flex;gap:10px;align-items:center;padding:13px max(18px,calc((100vw - 1440px)/2));border-bottom:1px solid var(--line);background:rgba(255,255,255,.94);backdrop-filter:blur(12px)}.search-wrap{position:relative;flex:1}.search-wrap input{width:100%;padding:12px 44px 12px 16px;border:1px solid #d8deea;border-radius:11px;background:#fff;font-size:15px;outline:none}.search-wrap input:focus{border-color:var(--brand2);box-shadow:0 0 0 3px rgba(84,116,216,.14)}.shortcut{position:absolute;right:12px;top:10px;padding:2px 7px;border:1px solid var(--line);border-radius:6px;color:var(--muted);font-size:12px}.toolbar button{padding:11px 14px;border:1px solid #d8deea;border-radius:10px;background:#fff;color:var(--text);cursor:pointer}.toolbar button:hover{border-color:var(--brand2);color:var(--brand)}.visible-count{white-space:nowrap;color:var(--muted);font-size:13px}
main{max-width:1440px;margin:24px auto;padding:0 18px 60px}.stock-card{margin-bottom:14px;border:1px solid var(--line);border-radius:16px;background:var(--panel);box-shadow:0 2px 10px rgba(27,39,73,.035);overflow:hidden}.stock-card[hidden]{display:none}.stock-card summary{display:flex;justify-content:space-between;gap:16px;align-items:center;padding:18px 20px;cursor:pointer;list-style:none}.stock-card summary::-webkit-details-marker{display:none}.stock-card details[open] summary{border-bottom:1px solid var(--line);background:#fbfcff}.stock-identity,.stock-glance{display:flex;align-items:center;gap:10px}.stock-code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:17px;color:var(--brand)}.stock-name{font-size:18px}.market-badge{padding:3px 8px;border-radius:999px;background:#edf1fb;color:#405aa5;font-size:12px}.stock-glance{color:var(--muted);font-size:13px}.chevron{font-size:20px;transition:transform .2s}.stock-card details[open] .chevron{transform:rotate(180deg)}.stock-body{padding:16px}.reason-card{margin-bottom:16px;padding:18px;border:1px solid var(--line);border-radius:14px;background:#fff}.reason-card:last-child{margin-bottom:0}.reason-card h3{margin:0 0 14px;font-size:16px}.metrics{display:grid;grid-template-columns:repeat(6,minmax(110px,1fr));gap:9px;margin-bottom:16px}.metric{padding:10px 12px;border-radius:10px;background:#f7f8fb}.metric span{display:block;color:var(--muted);font-size:11px}.metric strong{font-size:14px}.positive{color:var(--positive)!important}.negative{color:var(--negative)!important}.neutral{color:var(--text)}
.seat-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.seat-panel{min-width:0;border:1px solid var(--line);border-radius:12px;overflow:hidden}.seat-panel h4{display:flex;align-items:center;gap:8px;margin:0;padding:11px 13px;font-size:14px}.seat-panel.buy h4{background:var(--buy)}.seat-panel.sell h4{background:var(--sell)}.side-dot{width:8px;height:8px;border-radius:50%;background:var(--positive)}.seat-panel.sell .side-dot{background:var(--negative)}.table-scroll{overflow:auto}table{width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap}th,td{padding:9px 10px;border-top:1px solid var(--line);text-align:right}th{color:var(--muted);font-weight:600;background:#fafbfc}th:nth-child(2),td:nth-child(2){text-align:left}.rank{width:45px;text-align:center!important}.department{min-width:220px;white-space:normal}.money{font-variant-numeric:tabular-nums}.empty-row{text-align:center!important;color:var(--muted);padding:20px}.empty-state{padding:80px 20px;text-align:center;color:var(--muted)}
footer{padding:25px 18px 50px;text-align:center;color:var(--muted);font-size:12px}
@media(max-width:980px){.metrics{grid-template-columns:repeat(3,1fr)}.seat-grid{grid-template-columns:1fr}.toolbar button{display:none}}
@media(max-width:640px){.hero{padding:28px 18px}.overview{grid-template-columns:1fr 1fr}.overview-card:last-child{grid-column:1/-1}.toolbar{padding:10px}.visible-count{display:none}main{padding:0 10px}.stock-card summary{align-items:flex-start;padding:15px}.stock-identity{flex-wrap:wrap}.stock-glance>span:first-child{display:none}.metrics{grid-template-columns:1fr 1fr}.stock-body{padding:10px}.reason-card{padding:12px}.department{min-width:180px}}
@media print{.toolbar{display:none}.hero{padding:20px;background:#fff;color:#000}.stock-card{break-inside:avoid;box-shadow:none}.stock-card details:not([open])>*:not(summary){display:block}.stock-card details:not([open]) summary{border-bottom:1px solid var(--line)}main{max-width:none}.seat-grid{grid-template-columns:1fr 1fr}}
</style>
</head>
<body>
"""
    body = """
<header class="hero">
  <div class="hero-kicker">CNINFO · DRAGON TIGER LIST</div>
  <h1>每日龙虎榜完整报告</h1>
  <div class="hero-date">交易日期：{trade_date}</div>
  <div class="overview">
    <div class="overview-card"><span>上榜证券</span><strong>{stocks}</strong></div>
    <div class="overview-card"><span>上榜记录</span><strong>{records}</strong></div>
    <div class="overview-card"><span>买卖席位</span><strong>{seats}</strong></div>
  </div>
</header>
<nav class="toolbar">
  <div class="search-wrap">
    <input id="search" type="search" autocomplete="off" placeholder="搜索代码、名称、上榜原因或营业部…">
    <span class="shortcut">/</span>
  </div>
  <button id="expand" type="button">全部展开</button>
  <button id="collapse" type="button">全部折叠</button>
  <span id="visible-count" class="visible-count">共 {stocks} 只</span>
</nav>
<main id="stocks">{cards}{empty}</main>
<footer>数据来源：<a href="{source}" target="_blank" rel="noreferrer">东方财富龙虎榜</a> · 仅供归档研究，不构成投资建议</footer>
<script>
const cards=[...document.querySelectorAll('.stock-card')];
const search=document.getElementById('search');
const counter=document.getElementById('visible-count');
function applySearch(){
  const q=search.value.trim().toLowerCase();let visible=0;
  cards.forEach(card=>{const hit=!q||card.dataset.search.includes(q);card.hidden=!hit;if(hit){visible++;if(q)card.querySelector('details').open=true;}});
  counter.textContent=`显示 ${visible} / ${cards.length} 只`;
}
search.addEventListener('input',applySearch);
document.getElementById('expand').addEventListener('click',()=>cards.filter(c=>!c.hidden).forEach(c=>c.querySelector('details').open=true));
document.getElementById('collapse').addEventListener('click',()=>cards.forEach(c=>c.querySelector('details').open=false));
document.addEventListener('keydown',event=>{if(event.key==='/'&&document.activeElement!==search){event.preventDefault();search.focus();}});
</script>
</body>
</html>
""".format(
        trade_date=_text(trade_date),
        stocks=len(records_by_code),
        records=len(records),
        seats=seat_count,
        cards="".join(cards),
        empty=empty,
        source=_attribute(source_page),
    )
    return head + body


def write_html_report(
    path: Path,
    *,
    trade_date: str,
    records: list[dict[str, Any]],
    source_page: str = SOURCE_PAGE,
) -> None:
    path.write_text(
        build_html(trade_date=trade_date, records=records, source_page=source_page),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def run(
    *,
    trade_date: str,
    output_dir: Path,
    upload_drive: bool = False,
    drive_client: AppsScriptDriveClient | None = None,
) -> dict[str, Any]:
    target = Path(output_dir) / trade_date
    json_path = target / f"龙虎榜_{trade_date}.json"
    manifest_path = target / "manifest.json"
    if not json_path.exists() or not manifest_path.exists():
        raise RuntimeError("请先运行 src.dragon_tiger 生成 JSON 和 manifest")

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    records = payload.get("records") or []
    if not isinstance(records, list):
        raise RuntimeError("龙虎榜 JSON records 格式异常")
    html_path = target / f"龙虎榜报告_{trade_date}.html"
    write_html_report(
        html_path,
        trade_date=trade_date,
        records=[item for item in records if isinstance(item, dict)],
        source_page=str(payload.get("source") or SOURCE_PAGE),
    )
    descriptor = {
        "name": html_path.name,
        "size": html_path.stat().st_size,
        "sha256": _sha256(html_path),
    }
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"] = [
        item for item in manifest.get("files", []) if item.get("name") != html_path.name
    ] + [descriptor]

    result: dict[str, Any] = {
        "phase": "complete",
        "trade_date": trade_date,
        "html_file": descriptor,
        "upload_drive_requested": upload_drive,
        "drive_status": "not_requested",
        "drive_path": None,
        "failures": 0,
        "errors": [],
    }
    if upload_drive:
        client = drive_client or AppsScriptDriveClient()
        try:
            client.ping()
            upload = client.upload_dataset_file(
                html_path,
                dataset="dragon_tiger",
                data_date=trade_date,
            )
            status = str(upload["status"])
            result["drive_status"] = status
            result["drive_path"] = upload.get("drive_path")
            manifest[f"drive_{status}"] = int(manifest.get(f"drive_{status}") or 0) + 1
            manifest["drive_files"] = [
                item
                for item in manifest.get("drive_files", [])
                if item.get("name") != html_path.name
            ] + [
                {
                    "name": html_path.name,
                    "status": status,
                    "drive_path": upload.get("drive_path"),
                    "file_id": upload.get("file_id"),
                }
            ]
        except Exception as exc:
            result["phase"] = "failed"
            result["failures"] = 1
            result["errors"] = [str(exc)]
            manifest["phase"] = "failed"
            manifest["drive_failed"] = int(manifest.get("drive_failed") or 0) + 1
            manifest["failures"] = int(manifest.get("failures") or 0) + 1
            manifest["errors"] = [*manifest.get("errors", []), f"{html_path.name}: {exc}"]

    manifest["html_report"] = result
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成并归档龙虎榜 HTML 阅读报告")
    parser.add_argument("--date", required=True, help="交易日期 YYYY-MM-DD")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/dragon-tiger"),
        help="龙虎榜输出根目录",
    )
    parser.add_argument("--upload-drive", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run(
        trade_date=args.date,
        output_dir=args.output,
        upload_drive=args.upload_drive,
    )
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
